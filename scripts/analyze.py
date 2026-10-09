#!/usr/bin/env python3
"""Validate immutable attempts, summarize exact/merged groups and matched repetitions."""
import argparse
import csv
import json
from pathlib import Path
import statistics
from validation import load_done, normalize_plan

DIMENSIONS = ['variant','source_commit','binary_sha256','connections','inflight_limit','pipeline_batch',
              'workload','target_commands_s','duration_s','warmup_s','idle_s','workers','spike_mode',
              'group_scope','group','payload_bytes','diagnostic','allocator']

def median(values): return statistics.median(values) if values else None

def slope(samples):
    if len(samples)<2: return None
    x=[s['elapsed_s'] for s in samples]; y=[s['rss_bytes']/2**20 for s in samples]
    xm,ym=statistics.mean(x),statistics.mean(y)
    denominator=sum((v-xm)**2 for v in x)
    return sum((a-xm)*(b-ym) for a,b in zip(x,y))/denominator*60 if denominator else None

def rss_metrics(samples, summary):
    cfg=summary['config']; start=summary['measure_start_s']; end=start+cfg['duration_s']
    measured=[s for s in samples if s['phase']==1 and s['rss_bytes'] is not None and start<=s['elapsed_s']<=end]
    steady=[s for s in measured if end-min(120,cfg['duration_s'])<=s['elapsed_s']]
    drained=[s for s in samples if s['phase']==1 and s['rss_bytes'] is not None]
    values=lambda xs:[s['rss_bytes']/2**20 for s in xs]
    late=lambda phase: [s for s in samples if s['phase']==phase and s['rss_bytes'] is not None]
    idle=late(2); dropped=late(3)
    if idle: idle=[s for s in idle if s['elapsed_s']>=idle[-1]['elapsed_s']-5]
    return dict(steady_rss_mib=median(values(steady)),average_rss_mib=statistics.mean(values(measured)) if measured else None,
                sampled_peak_rss_mib=max(values(measured),default=None),
                sampled_peak_including_drain_mib=max(values(drained),default=None),
                process_peak_including_warmup_mib=summary['process_peak_rss_bytes_includes_warmup']/2**20,
                rss_slope_mib_per_min=slope(measured),steady_rss_slope_mib_per_min=slope(steady),
                steady_rss_min_mib=min(values(steady),default=None),steady_rss_max_mib=max(values(steady),default=None),
                idle_final_rss_mib=median(values(idle)),after_drop_rss_mib=median(values(dropped)),
                average_inflight_commands=statistics.mean(s['inflight_commands'] for s in measured) if measured else None,
                average_inflight_payload_mib=statistics.mean(s['inflight_payload_bytes']/2**20 for s in measured) if measured else None)

def parse_counters(row):
    result={}
    if row.get('proc_stat'):
        cpu=list(map(int,row['proc_stat'].splitlines()[0].split()[1:9]))
        # Exclude guest time: already included in user/nice.
        result['host_cpu_total_ticks']=sum(cpu)
        result['host_cpu_idle_ticks']=cpu[3]+cpu[4]
    if row.get('net_dev'):
        for line in row['net_dev'].splitlines()[2:]:
            name, data=line.split(':'); values=list(map(int,data.split()));name=name.strip()
            for metric,index in [('rx_bytes',0),('rx_errors',2),('rx_drops',3),('tx_bytes',8),('tx_errors',10),('tx_drops',11)]:
                result['net_'+name+'_'+metric]=values[index]
    if row.get('net_snmp'):
        lines=row['net_snmp'].splitlines()
        for header,values in zip(lines[::2],lines[1::2]):
            if header.startswith('Tcp:'):
                for name,value in zip(header.split()[1:],values.split()[1:]):
                    if name in ['RetransSegs','InErrs','OutRsts']: result['tcp_'+name]=int(value)
    return result

def host_metrics(attempt, summary):
    start=summary['epoch_unix_s']+summary['measure_start_s']; end=start+summary['config']['duration_s']
    rows=[json.loads(line) for line in (attempt/'host.jsonl').read_text().splitlines()]
    rows=[r for r in rows if start<=r.get('unix_s',0)<=end]
    if len(rows)<2: return {'host_sample_span_s':None,'server_info_samples':0}
    a,b=parse_counters(rows[0]),parse_counters(rows[-1]); span=rows[-1]['unix_s']-rows[0]['unix_s']
    metrics={'host_sample_span_s':span}
    for key in a.keys() & b.keys():
        delta=b[key]-a[key]
        if delta<0: raise ValueError('Host counter reset: '+key)
        metrics[key+'_delta']=delta
    total=metrics.get('host_cpu_total_ticks_delta',0)
    if total: metrics['host_busy_pct']=100*(1-metrics['host_cpu_idle_ticks_delta']/total)
    server=[r for r in rows if r.get('server_info',{}).get('counters')]
    metrics['server_info_samples']=len(server)
    if len(server)>=2:
        first,last=server[0]['server_info']['counters'],server[-1]['server_info']['counters']
        metrics['server_info_sample_span_s']=server[-1]['unix_s']-server[0]['unix_s']
        for key in first.keys() & last.keys():
            if key.startswith(('used_cpu_', 'total_')):
                metrics['endpoint_'+key+'_delta']=last[key]-first[key]
            else: metrics['endpoint_'+key+'_final']=last[key]
    return metrics

def run_rows(summary, samples, attempt, build):
    cfg=summary['config']
    base={k:cfg[k] for k in ['id','variant','connections','pipeline_batch','workload','duration_s','warmup_s','idle_s','workers','spike_mode']}
    base.update(source_commit=summary['build_commit'],binary_sha256=build['binary_sha256'],
                inflight_limit=cfg['connections']*cfg['per_connection_inflight'],target_commands_s=cfg['rate'],
                achieved_commands_s=summary['achieved_commands_s'],status=summary['status'],errors=summary['errors'],
                total_commands=summary['completed'],offered=summary['offered'],not_submitted=summary['not_submitted'],
                capacity_skipped=summary['capacity_skipped'],deadline_skipped=summary['deadline_skipped'],
                user_cpu_s=summary['user_cpu_s'],system_cpu_s=summary['system_cpu_s'],
                total_cpu_s=summary['user_cpu_s']+summary['system_cpu_s'],cpu_us_per_command=summary['cpu_us_per_command'],
                drain_s=summary['drain_s'],large_connections_covered=sum(n>0 for n in summary['large_commands_per_connection']),
                large_commands_min_per_connection=min(summary['large_commands_per_connection']),
                peak_inflight_commands=summary['peak_inflight_commands'],peak_inflight_payload_mib=summary['peak_inflight_payload_bytes']/2**20,
                diagnostic=summary['allocation_diagnostics'] or summary['codec_diagnostics'],allocator=summary['allocator'])
    base.update(rss_metrics(samples,summary));base.update(host_metrics(attempt,summary))
    for category,metrics in summary['diagnostic_end'].items():
        before=summary['diagnostic_start'][category]
        for name,value in metrics.items():
            base[category+'_'+name+'_end']=value
            # Gauges and maxima are levels, not event deltas.
            if not name.startswith(('live_','peak_','max_')):
                base[category+'_'+name+'_delta']=value-before[name]
    rows=[]
    for scope,groups in [('aggregate',summary['aggregate_groups']),('payload',summary['groups'])]:
        for name,group in groups.items():
            row=dict(base,group_scope=scope,group=name,payload_bytes=int(name) if scope=='payload' else None,group_commands=group['commands'])
            for kind in ['response','scheduled_to_response','scheduling']:
                for metric in ['mean_us','p50_us','p99_us','p999_us','max_us','p999_reportable']:
                    row[kind+'_'+metric]=None if metric=='p999_us' and not group[kind]['p999_reportable'] else group[kind][metric]
            rows.append(row)
    return rows

def median_rows(rows):
    grouped={}
    for row in rows: grouped.setdefault(tuple(row[d] for d in DIMENSIONS),[]).append(row)
    result=[]
    for key,runs in grouped.items():
        row=dict(zip(DIMENSIONS,key),repetitions=len(runs),limited_runs=sum(r['status']=='LIMITED' for r in runs))
        for field in set().union(*(r.keys() for r in runs)):
            values=[r[field] for r in runs if isinstance(r.get(field),(int,float)) and not isinstance(r[field],bool)]
            if field not in DIMENSIONS and values: row[field]=statistics.median(values)
        result.append(row)
    return result

def write_csv(path,rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader();writer.writerows(rows)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('results',type=Path);args=p.parse_args()
    plan=normalize_plan(json.loads((args.results/'plan.json').read_text()))
    builds=json.loads((args.results/'builds.json').read_text())
    environment=json.loads((args.results/'environment.json').read_text())
    linux=environment['platform'].startswith('Linux')
    rows=[];completed=0
    for cfg in plan:
        parent=args.results/cfg['id']
        if not (parent/'DONE.json').exists(): continue
        build=builds['baseline' if cfg['variant']=='baseline' else 'patched']
        summary,samples,attempt=load_done(parent,cfg,build,linux)
        rows.extend(run_rows(summary,samples,attempt,build));completed+=1
    if not rows: raise SystemExit('No validated completed runs')
    write_csv(args.results/'per_run.csv',rows);write_csv(args.results/'medians.csv',median_rows(rows))
    print(f'Verified completed runs: {completed}/{len(plan)}; remaining: {len(plan)-completed}')
    print('CPU/RSS are whole-run metrics repeated for each latency group; do not sum them across groups.')
    print('CPU/command and all latencies use microseconds; RSS uses MiB. Unreportable p99.9 is blank.')
    print('RSS slopes are observations, not proof of a leak or steady state.')
    print('Host deltas cover their reported sample span; no server CPU/bottleneck attribution without server telemetry.')
    print('Medians of run percentiles are not pooled percentiles. HDR files retain the distributions.')
    print('CSV files:',args.results/'per_run.csv',args.results/'medians.csv')

if __name__=='__main__': main()
