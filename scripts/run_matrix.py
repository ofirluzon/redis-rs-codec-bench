#!/usr/bin/env python3
"""Explicit launch only. Immutable attempt folders and resumable completed runs."""
import argparse
import datetime
import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import shutil
import tempfile
import time
import urllib.request

from validation import normalize_plan, validate_summary, validate_artifacts, checksums, load_done

ROOT=Path(__file__).resolve().parents[1]

def command(argv):
    try:
        p=subprocess.run(argv,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
        return {'returncode':p.returncode,'output':p.stdout}
    except (OSError,subprocess.TimeoutExpired) as e:return {'unavailable':str(e)}

def textfile(path):
    try:return Path(path).read_text()
    except OSError:return None

def instance_metadata():
    """IMDSv2 instance type only; no identifiers, location, credentials or role metadata."""
    if platform.system()!='Linux':return {'unavailable':'non_linux_local_check'}
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req=urllib.request.Request('http://169.254.169.254/latest/api/token',method='PUT',
                                   headers={'X-aws-ec2-metadata-token-ttl-seconds':'60'})
        with opener.open(req,timeout=1) as response:token=response.read().decode()
        result={}
        for name in ['instance-type']:
            req=urllib.request.Request('http://169.254.169.254/latest/meta-data/'+name,
                                       headers={'X-aws-ec2-metadata-token':token})
            with opener.open(req,timeout=1) as response:result[name]=response.read().decode()
        return result
    except Exception as e:return {'unavailable':type(e).__name__}

def environment(server_info=False):
    return {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'platform':platform.platform(),
        'machine':platform.machine(),'instance':instance_metadata(),
        'target_identity_sha256':hashlib.sha256(os.environ['REDIS_URL'].encode()).hexdigest(),
        'host_identity_sha256':hashlib.sha256((textfile('/etc/machine-id') or platform.node()).encode()).hexdigest(),
        'lscpu':command(['lscpu']),
        'glibc':command(['ldd','--version']),'packages':command(['rpm','-q','glibc','kernel','sysstat','perf']),
        'rustc':command([str(Path.home()/'.cargo/bin/rustc'),'-Vv']),
        'os_release':textfile('/etc/os-release'),'meminfo':textfile('/proc/meminfo'),
        'limits':textfile('/proc/self/limits'),'cgroup':textfile('/proc/self/cgroup'),
        'memory_max':textfile('/sys/fs/cgroup/memory.max'),'cpu_max':textfile('/sys/fs/cgroup/cpu.max'),
        'allocator_environment':{k:os.environ.get(k) for k in ['LD_PRELOAD','MALLOC_CONF','MALLOC_ARENA_MAX','MALLOC_TRIM_THRESHOLD_','GLIBC_TUNABLES']},
        'server_info_enabled':server_info,
        'server_telemetry':'Optional INFO covers only one endpoint; server-node CPU/telemetry requires separate collection. ECHO is not server-free.'}

def telemetry(path,pid,stop,server_info=False):
    started=time.monotonic()
    with path.open('w') as writer:
        while not stop.is_set():
            row={'elapsed_s':time.monotonic()-started,'unix_s':time.time(),
                'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'proc_stat':textfile('/proc/stat'),'meminfo':textfile('/proc/meminfo'),
                'net_dev':textfile('/proc/net/dev'),'net_snmp':textfile('/proc/net/snmp'),
                'net_netstat':textfile('/proc/net/netstat'),'pressure_memory':textfile('/proc/pressure/memory'),
                'pressure_cpu':textfile('/proc/pressure/cpu'),'process_status':textfile(f'/proc/{pid}/status')}
            if int(row['elapsed_s'])%5==0:row['smaps_rollup']=textfile(f'/proc/{pid}/smaps_rollup')
            if server_info and int(row['elapsed_s'])%5==0:
                from server_info import collect
                row['server_info']=collect(os.environ['REDIS_URL'])
            writer.write(json.dumps(row)+'\n');writer.flush()
            stop.wait(1)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('plan',type=Path)
    p.add_argument('--results',type=Path,required=True)
    p.add_argument('--diagnostics',action='store_true')
    p.add_argument('--jemalloc',action='store_true')
    p.add_argument('--server-info',action='store_true',help='Sample INFO ALL from the target endpoint every five seconds; requires redis-cli')
    p.add_argument('--allow-non-linux',action='store_true',help='Local correctness checks only')
    args=p.parse_args()
    if 'REDIS_URL' not in os.environ:p.error('Set REDIS_URL before launch')
    if platform.system()!='Linux' and not args.allow_non_linux:p.error('Scored runs require Linux')
    if args.diagnostics and args.jemalloc:p.error('Separate diagnostic and jemalloc experiments')
    try:
        document=json.loads(args.plan.read_text())
        if isinstance(document,dict):
            if set(document)!={'schema','mode','cases'} or document['schema']!=1 or document['mode'] not in ['scored','diagnostic','jemalloc']:
                raise ValueError('Invalid plan envelope')
            mode=document['mode']
            if (args.diagnostics and mode!='diagnostic') or (args.jemalloc and mode!='jemalloc'):
                raise ValueError('CLI build mode contradicts the frozen plan')
            args.diagnostics=mode=='diagnostic';args.jemalloc=mode=='jemalloc'
            document=document['cases']
        rows=normalize_plan(document)
    except (ValueError, TypeError) as e: p.error(str(e))
    args.results.mkdir(parents=True,exist_ok=True)
    # OS releases this exclusive lock on interruption; a stale file cannot block resume.
    lock=(args.results/'.runner.lock').open('a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: p.error('Another runner is using this results directory')
    host_lock=(Path(tempfile.gettempdir())/f'redis-rs-codec-bench-{os.getuid()}.lock').open('a')
    try: fcntl.flock(host_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: p.error('Another benchmark matrix is running on this host')
    plan_copy=args.results/'plan.json'
    if plan_copy.exists() and json.loads(plan_copy.read_text())!=rows:p.error('Different plan already stored in results directory')
    plan_copy.write_text(json.dumps(rows,indent=2))
    fingerprint=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    previous_locks=set()
    builds={}
    for name in ['baseline','patched']:
        suffix='-diagnostic' if args.diagnostics else '-jemalloc' if args.jemalloc else ''
        binary=ROOT/'bin'/(name+suffix)
        meta=json.loads(binary.with_suffix('.build.json').read_text())
        if hashlib.sha256(binary.read_bytes()).hexdigest()!=meta['binary_sha256']:p.error('Binary checksum mismatch')
        expected_features=(['patched'] if name=='patched' else []) + (['alloc-diagnostics','codec-diagnostics'] if args.diagnostics else ['jemalloc'] if args.jemalloc else [])
        if set(meta['features'])!=set(expected_features): p.error('Build feature mode mismatch')
        for field,path in [('harness_sha256',ROOT/'src/main.rs'),('lock_sha256',ROOT/'Cargo.lock'),('instrumentation_sha256',ROOT/'scripts/build.py')]:
            if meta.get(field)!=hashlib.sha256(path.read_bytes()).hexdigest(): p.error('Sources changed after build; rebuild: '+field)
        previous_locks.add(meta['lock_sha256'])
        builds[name]=meta
    if len(previous_locks)!=1:p.error('Baseline and patch dependency lockfiles differ')
    if len({m['harness_sha256'] for m in builds.values()})!=1:p.error('Baseline and patch harness sources differ')
    build_path=args.results/'builds.json'
    if build_path.exists() and json.loads(build_path.read_text())!=builds:
        p.error('Builds changed; use a fresh results directory')
    build_path.write_text(json.dumps(builds,indent=2))
    # Retain the exact public kit and binaries once per matrix, alongside its private results.
    snapshot=args.results/'kit';snapshot.mkdir(exist_ok=True)
    for directory in ['src','scripts']:
        for path in (ROOT/directory).glob('*'):
            if path.is_file() and path.suffix in ['.py','.rs','.sh']:
                dest=snapshot/directory/path.name;dest.parent.mkdir(exist_ok=True)
                if not dest.exists(): dest.write_bytes(path.read_bytes())
    for name in ['Cargo.toml','Cargo.lock','rust-toolchain.toml','matrix.json']:
        if not (snapshot/name).exists(): (snapshot/name).write_bytes((ROOT/name).read_bytes())
    for name in builds:
        binary=ROOT/'bin'/(name+suffix);dest=snapshot/'bin';dest.mkdir(exist_ok=True)
        if not (dest/binary.name).exists(): shutil.copy2(binary,dest/binary.name)
    env=environment(args.server_info)
    # A resumed matrix cannot quietly mix different machines or allocators.
    env_path=args.results/'environment.json'
    if env_path.exists():
        old=json.loads(env_path.read_text())
        for key in ['server_info_enabled','target_identity_sha256','host_identity_sha256','machine','platform','instance','glibc','allocator_environment']:
            if old.get(key)!=env.get(key):p.error('Environment changed; use a fresh results directory')
    else:env_path.write_text(json.dumps(env,indent=2))
    with (args.results/'progress.log').open('a') as progress:
        def log(message):
            line=datetime.datetime.now(datetime.timezone.utc).isoformat()+' '+message
            print(line,flush=True);progress.write(line+'\n');progress.flush()
        log('BEGIN plan='+fingerprint)
        for config in rows:
            parent=args.results/config['id'];parent.mkdir(exist_ok=True)
            done=parent/'DONE.json'
            if done.exists():
                kind='baseline' if config['variant']=='baseline' else 'patched'
                load_done(parent,config,builds[kind],platform.system()=='Linux')
                log('SKIP '+config['id']);continue
            attempts=list(parent.glob('attempt-*'))
            number=max([int(a.name.split('-')[1]) for a in attempts] or [0])+1
            out=parent/f'attempt-{number:03d}';out.mkdir()
            cfg=out/'config.json';cfg.write_text(json.dumps(config,indent=2))
            kind='baseline' if config['variant']=='baseline' else 'patched'
            suffix='-diagnostic' if args.diagnostics else '-jemalloc' if args.jemalloc else ''
            binary=ROOT/'bin'/(kind+suffix)
            build=json.loads(binary.with_suffix('.build.json').read_text())
            (out/'build.json').write_text(json.dumps(build,indent=2))
            log('START '+config['id'])
            with (out/'stdout.log').open('w') as stdout,(out/'stderr.log').open('w') as stderr:
                process=subprocess.Popen([str(binary),str(cfg),str(out)],stdout=stdout,stderr=stderr)
                stop=threading.Event();telemetry_errors=[]
                def sample_host():
                    try: telemetry(out/'host.jsonl',process.pid,stop,args.server_info)
                    except BaseException as error: telemetry_errors.append(error)
                sampler=threading.Thread(target=sample_host)
                sampler.start()
                try:returncode=process.wait(timeout=config['warmup_s']+config['duration_s']+config['idle_s']+180)
                except BaseException:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                    raise
                finally:stop.set();sampler.join()
            if telemetry_errors: raise RuntimeError('Host sampler failed') from telemetry_errors[0]
            if returncode!=0:log('FAILED '+config['id']);raise SystemExit('Run failed; incomplete attempt retained separately')
            summary=json.loads((out/'summary.json').read_text())
            failures=validate_summary(config,summary,build)
            if failures:
                log('INVALID '+config['id']+' '+','.join(failures));raise SystemExit('Validation failed')
            validate_artifacts(out,summary,platform.system()=='Linux')
            completed={'attempt':out.name,'status':summary['status'],'build_sha256':build['binary_sha256'],'files_sha256':checksums(out)}
            pending=parent/'DONE.tmp';pending.write_text(json.dumps(completed,indent=2));pending.replace(done)
            log(summary['status']+' '+config['id']+f" achieved={summary['achieved_commands_s']:.3f}/s")
            if config['workload']=='rare' and any(n==0 for n in summary['large_commands_per_connection']):
                log('COVERAGE_INCOMPLETE '+config['id']+' some connections saw no large payload')
        log('ALL_DONE')

if __name__=='__main__':main()
