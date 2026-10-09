#!/usr/bin/env python3
"""Correctness check on an isolated loopback Redis. Never targets AWS."""
import json
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jemalloc',action='store_true')
    args=parser.parse_args()
    server=shutil.which('redis-server')
    if not server:raise SystemExit('redis-server needed for local correctness check')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    timestamp=time.strftime('%Y%m%d-%H%M%S')
    out=ROOT/'results'/('local-check-'+timestamp);out.mkdir(parents=True)
    with (out/'server.log').open('w') as log:
        process=subprocess.Popen([server,'--bind','127.0.0.1','--port',str(port),'--save','','--appendonly','no'],stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=0.1):break
                except OSError:time.sleep(0.05)
            else:raise RuntimeError('Local server did not start')
            env=dict(os.environ,REDIS_URL=f'redis://127.0.0.1:{port}')
            rows=[]
            for variant in ['baseline','patched-off','patched-on']:
                for workload,connections,batch in [('small',1,1),('rare',4,1),('near',1,1),('large',1,4)]:
                    rows.append({'id':f'local-{variant}-{workload}-c{connections}-b{batch}',
                        'variant':variant,'workload':workload,'connections':connections,'per_connection_inflight':4,
                        'rate':2000 if workload=='rare' else 51 if workload=='large' else 50,'duration_s':2,'warmup_s':0,'idle_s':0,
                        'workers':4,'pipeline_batch':batch,'seed':42,'spike_mode':'random'})
            plan=out/'local-plan.json';plan.write_text(json.dumps(rows,indent=2))
            scored=out/'scored'
            command=['python3',str(ROOT/'scripts/run_matrix.py'),str(plan),'--results',str(scored),'--allow-non-linux','--server-info']
            if args.jemalloc: command.append('--jemalloc')
            subprocess.run(command,check=True,env=env)
            # A repeated invocation must resume without adding attempts or mixing configurations.
            attempts_before=len(list(scored.glob('*/attempt-*')))
            subprocess.run(command,check=True,env=env)
            assert len(list(scored.glob('*/attempt-*')))==attempts_before
            from server_info import collect
            assert collect(env['REDIS_URL']).get('counters',{}).get('connected_clients',0)>0
            for done in scored.glob('*/DONE.json'):
                attempt=done.parent/json.loads(done.read_text())['attempt']
                summary=json.loads((attempt/'summary.json').read_text())
                assert summary['aggregate_groups']['all']['commands']==summary['completed']
                if summary['config']['workload']=='large':
                    assert summary['offered']==102
                    assert summary['completed']==102, 'Tail pipeline command was lost in loopback'
                assert summary['allocator']==('jemalloc' if args.jemalloc else 'system')
            subprocess.run(['python3',str(ROOT/'scripts/analyze.py'),str(scored)],check=True)
            # Reports preserve individual cases and the validated total; local checks are one run per case.
            import csv
            for name in ['per_run.csv','means.csv','medians.csv','report.md','payload_report.md']:
                assert (scored/name).stat().st_size>0
            with (scored/'means.csv').open() as stream:
                assert all(int(r['repetitions'])==1 for r in csv.DictReader(stream))
            assert 'Validated runs: **12/12**' in (scored/'report.md').read_text()
            assert 'INCOMPLETE' not in (scored/'report.md').read_text()
            assert (scored/'kit/docs/METRICS.md').is_file()
            for diagnostic in ([False] if args.jemalloc else [False,True]):
                for variant in ['baseline','patched-off','patched-on']:
                    binary='baseline' if variant=='baseline' else 'patched'
                    if diagnostic:binary+='-diagnostic'
                    elif args.jemalloc:binary+='-jemalloc'
                    case={'id':f'probe-{variant}-{diagnostic}','variant':variant,'workload':'single-spike',
                        'connections':1,'per_connection_inflight':16,'rate':50,'duration_s':7,'warmup_s':0,
                        'idle_s':1,'workers':4,'pipeline_batch':1,'seed':42,'spike_mode':'random'}
                    directory=out/case['id'];directory.mkdir()
                    cfg=directory/'config.json';cfg.write_text(json.dumps(case))
                    subprocess.run([str(ROOT/'bin'/binary),str(cfg),str(directory)],check=True,env=env)
                    summary=json.loads((directory/'summary.json').read_text())
                    assert summary['completed']==251 and summary['errors']==0
                    assert summary['large_commands_per_connection']==[1]
                    assert summary['not_submitted']==0 and summary['status']=='PROBE'
                    if diagnostic:
                        samples=[json.loads(line) for line in (directory/'samples.jsonl').read_text().splitlines()]
                        assert all(s['alloc']['live_requested_bytes']>=0 for s in samples)
                        assert samples[-1]['codec']['read_calls']>0
                        trims=sum(samples[-1]['codec'][k] for k in ['read_trims_complete','read_trims_partial','write_trims'])
                        assert (trims>0)==(variant=='patched-on')
            # Connection failure must not produce a valid DONE result.
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));bad_port=sock.getsockname()[1]
            bad_env=dict(env,REDIS_URL=f'redis://127.0.0.1:{bad_port}')
            failure=out/'failure'
            p=subprocess.run(['python3',str(ROOT/'scripts/run_matrix.py'),str(plan),'--results',str(failure),'--allow-non-linux'],env=bad_env)
            assert p.returncode!=0 and not list(failure.glob('*/DONE.json'))
            print('LOCAL_CHECK_PASS',out)
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()

if __name__=='__main__':main()
