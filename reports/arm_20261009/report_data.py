"""Load the frozen October ARM experiment; reject unrelated or changed observations."""
import argparse
import csv
import hashlib
import json
import math
import pathlib
import re
import statistics
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parents[1] / 'scripts'))
from validation import load_done, normalize_plan


def artifact_root():
    parser = argparse.ArgumentParser(description='Reproduce the October 2026 ARM report from the retained private artifact directory.')
    parser.add_argument('artifacts', type=pathlib.Path, help='Directory containing results/main, results/knee-validation and results/small-capacity-pilot; derived reports are written here.')
    root = parser.parse_args().artifacts.resolve()
    if not (root / 'results').is_dir():
        parser.error('Artifact directory must contain results/')
    return root


def interpretation(root, key):
    """Read private experiment commentary separately from public rendering code."""
    path = root / 'interpretation' / 'arm_20261009.json'
    if not path.is_file():
        raise ValueError('Missing private interpretation/arm_20261009.json; see docs/HTML_REPORTS.md')
    return json.loads(path.read_text())[key]


def validate_saved_rows(stage, filename, rows):
    manifest = json.loads((SCRIPT_DIR / 'metrics-sha256.json').read_text())
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != manifest[stage][filename]:
        raise ValueError(f'{stage}/{filename} does not match the frozen October experiment; use scripts/analyze.py for other runs')


def validate_report_data(data, root):
    """Reject changed/stale embedded metrics before attaching the saved interpretation."""
    expected_stages = {'main', 'knee-validation'}
    for kind, filename in [('rows', 'per_run.csv'), ('means', 'means.csv'), ('medians', 'medians.csv')]:
        if {r.get('stage') for r in data[kind]} != expected_stages:
            raise ValueError('Combined report must contain both October measurement groups')
        for stage in sorted(expected_stages):
            with (root/'results'/stage/filename).open() as f:
                saved = list(csv.DictReader(f))
            validate_saved_rows(stage, filename, saved)
            embedded = [r for r in data[kind] if r['stage'] == stage]
            if len(embedded) != len(saved) or any(
                    {k: row.get(k) for k in reference} != reference
                    for row, reference in zip(embedded, saved)):
                raise ValueError(f'Embedded {stage}/{filename} differs from validated observations')


def load_stage(root, stage):
    P = root / 'results' / stage
    plan_by_id = {c['id']: c for c in normalize_plan(json.loads((P/'plan.json').read_text()))}
    rows = list(csv.DictReader((P / 'per_run.csv').open()))
    means = list(csv.DictReader((P / 'means.csv').open()))
    medians = list(csv.DictReader((P / 'medians.csv').open()))
    for filename, saved in [('per_run.csv', rows), ('means.csv', means), ('medians.csv', medians)]:
        validate_saved_rows(stage, filename, saved)
    allrows = [r for r in rows if r['group_scope'] == 'aggregate' and r['group'] == 'all']
    assert len(allrows) == 135 and all(r['status'] == 'OK' and r['errors'] == '0' for r in allrows)
    config = lambda r: (r['workload'], int(float(r['target_commands_s'])), int(r['connections']))
    configs = sorted(set(map(config, allrows)), key=lambda c: (['small','rare','near','large'].index(c[0]),c[1],c[2]))
    variants = ['baseline','patched-off','patched-on']
    for c in configs:
        for v in variants:
            rr = [r for r in allrows if config(r)==c and r['variant']==v]
            assert len(rr)==3 and {re.search(r'-r(\d)-',r['id'])[1] for r in rr}=={'1','2','3'}
            m = next(r for r in means if config(r)==c and r['variant']==v and r['group']=='all' and r['group_scope']=='aggregate')
            for k in ['cpu_us_per_command','total_cpu_s','steady_rss_mib','response_p99_us','achieved_commands_s']:
                assert math.isclose(float(m[k]), statistics.mean(float(r[k]) for r in rr), rel_tol=1e-12)
    if len(plan_by_id) != 135 or set(plan_by_id) != {r['id'] for r in allrows}:
        raise ValueError('Frozen plan does not match the scored cases')
    builds=json.loads((P/'builds.json').read_text())
    for v,b in builds.items():
        for f,k in [('bin/'+v,'binary_sha256'),('src/main.rs','harness_sha256'),('Cargo.lock','lock_sha256'),('scripts/build.py','instrumentation_sha256')]:
            if hashlib.sha256((P/'kit'/f).read_bytes()).hexdigest()!=b[k]:
                raise ValueError('Retained kit hash mismatch: '+f)

    def kv(text):
        return {k: int(v.split()[0]) for k,v in (line.split(':',1) for line in text.splitlines())}
    def cores(h):
        return {l.split()[0]: list(map(int,l.split()[1:9])) for l in h['proc_stat'].splitlines() if re.match(r'cpu\d+ ',l)}
    extra = {}
    for r in allrows:
        build=builds['baseline' if r['variant']=='baseline' else 'patched']
        if r['source_commit'] != build['commit'] or r['binary_sha256'] != build['binary_sha256']:
            raise ValueError('CSV build identity mismatch')
        parent=P/r['id']
        s, samples, a = load_done(parent, plan_by_id[r['id']], builds['baseline' if r['variant']=='baseline' else 'patched'], True)
        start=s['epoch_unix_s']+s['measure_start_s']; end=start+300
        hs=[json.loads(l) for l in (a/'host.jsonl').read_text().splitlines()]
        hs=[h for h in hs if start<=h['unix_s']<=end]
        first,last=cores(hs[0]),cores(hs[-1]); av=[]; peaks=[]
        for c in first:
            delta=[b-a for a,b in zip(first[c],last[c])]; t=sum(delta)
            if t: av.append(100*(1-(delta[3]+delta[4])/t))
        for h1,h2 in zip(hs,hs[1:]):
            x,y=cores(h1),cores(h2)
            for c in x:
                d=[b-a for a,b in zip(x[c],y[c])]; t=sum(d)
                if t: peaks.append(100*(1-(d[3]+d[4])/t))
        pressure=lambda field,which: [float(re.search(r'avg10=([\d.]+)',line)[1]) for h in hs for line in h.get(field,'').splitlines() if line.startswith(which+' ')]
        mem=[kv(h['meminfo']) for h in hs]
        e=dict(host_mem_available_min_gib=min(h['MemAvailable'] for h in mem)/2**20,
               host_swap_used_max_mib=max(h['SwapTotal']-h['SwapFree'] for h in mem)/1024,
               busiest_core_average_pct=max(av),busiest_core_one_sample_pct=max(peaks),
               cpu_pressure_some_avg10_max_pct=max(pressure('pressure_cpu','some'),default=0),
               memory_pressure_some_avg10_max_pct=max(pressure('pressure_memory','some'),default=0),
               memory_pressure_full_avg10_max_pct=max(pressure('pressure_memory','full'),default=0),
               nic_rx_gbit_s=float(r['net_ens34_rx_bytes_delta'])*8/float(r['host_sample_span_s'])/1e9,
               nic_tx_gbit_s=float(r['net_ens34_tx_bytes_delta'])*8/float(r['host_sample_span_s'])/1e9)
        extra[r['id']]=e
    with (root/(stage+'-host-details.csv')).open('w', newline='') as f:
        w=csv.DictWriter(f,fieldnames=['id']+list(next(iter(extra.values()))));w.writeheader()
        w.writerows(dict(id=k,**v) for k,v in extra.items())
    for r in rows:
        r.update(extra[r['id']])
        r['user_cpu_us_per_command']=float(r['user_cpu_s'])*1e6/int(r['total_commands'])
        r['system_cpu_us_per_command']=float(r['system_cpu_s'])*1e6/int(r['total_commands'])
    for output,reducer in [(means,statistics.mean),(medians,statistics.median)]:
        for m in output:
            rr=[r for r in rows if config(r)==config(m) and r['variant']==m['variant'] and r['group_scope']==m['group_scope'] and r['group']==m['group']]
            for k in list(next(iter(extra.values())))+['user_cpu_us_per_command','system_cpu_us_per_command']:
                m[k]=reducer(float(r[k]) for r in rr)

    return dict(rows=rows, means=means, medians=medians, allrows=allrows, configs=configs, extra=extra, builds=builds)


def validate_pilot(root):
    parent = root / 'results' / 'small-capacity-pilot'
    with (parent/'per_run.csv').open() as f:
        rows = list(csv.DictReader(f))
    validate_saved_rows('small-capacity-pilot', 'per_run.csv', rows)
    builds = json.loads((parent/'builds.json').read_text())
    for cfg in normalize_plan(json.loads((parent/'plan.json').read_text())):
        load_done(parent/cfg['id'], cfg, builds['baseline' if cfg['variant']=='baseline' else 'patched'], True)
    return [r for r in rows if r['group_scope']=='aggregate' and r['group']=='all']
