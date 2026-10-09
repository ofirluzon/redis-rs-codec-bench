#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path
import statistics

def main():
    p=argparse.ArgumentParser();p.add_argument('results',type=Path);args=p.parse_args()
    rows=[]
    for done in sorted(args.results.glob('*/DONE.json')):
        attempt=done.parent/json.loads(done.read_text())['attempt']
        summary=json.loads((attempt/'summary.json').read_text());cfg=summary['config']
        samples=[json.loads(line) for line in (attempt/'samples.jsonl').read_text().splitlines()]
        measured_and_drain=[s for s in samples if s['phase']==1 and s['rss_bytes'] is not None]
        # Submission window only: exclude drain and post-traffic idle from steady RSS.
        window_end=summary['measure_start_s']+cfg['duration_s']
        measured=[s for s in measured_and_drain if summary['measure_start_s']<=s['elapsed_s']<=window_end]
        steady=[s['rss_bytes'] for s in measured if window_end-min(120,cfg['duration_s'])<=s['elapsed_s']<=window_end]
        base={'id':cfg['id'],'variant':cfg['variant'],'connections':cfg['connections'],
              'inflight_limit':cfg['connections']*cfg['per_connection_inflight'],'pipeline_batch':cfg['pipeline_batch'],
              'workload':cfg['workload'],'target_commands_s':cfg['rate'],'achieved_commands_s':summary['achieved_commands_s'],
              'status':summary['status'],'errors':summary['errors'],'total_commands':summary['completed'],
              'user_cpu_s':summary['user_cpu_s'],'system_cpu_s':summary['system_cpu_s'],
              'cpu_us_per_command':summary['cpu_us_per_command'],'drain_s':summary['drain_s'],
              'steady_rss_mib':statistics.median(steady)/2**20 if steady else None,
              'average_rss_mib':statistics.mean(s['rss_bytes'] for s in measured)/2**20 if measured else None,
              'sampled_peak_rss_mib':max(s['rss_bytes'] for s in measured)/2**20 if measured else None,
              'sampled_peak_including_drain_mib':max(s['rss_bytes'] for s in measured_and_drain)/2**20 if measured_and_drain else None,
              'process_peak_including_warmup_mib':summary['process_peak_rss_bytes_includes_warmup']/2**20,
              'large_connections_covered':sum(n>0 for n in summary['large_commands_per_connection']),
              'peak_inflight_commands':summary['peak_inflight_commands'],
              'peak_inflight_payload_mib':summary['peak_inflight_payload_bytes']/2**20,
              'diagnostic':summary['allocation_diagnostics'] or summary['codec_diagnostics'],'allocator':summary['allocator']}
        for size,group in summary['groups'].items():
            row=dict(base,payload_bytes=int(size),group_commands=group['commands'])
            for kind in ['response','scheduled_to_response','scheduling']:
                for metric in ['mean_us','p50_us','p99_us','p999_us','max_us','p999_reportable']:
                    row[kind+'_'+metric]=group[kind][metric]
            rows.append(row)
    if not rows:raise SystemExit('No validated completed runs')
    with (args.results/'per_run.csv').open('w',newline='') as output:
        writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    dimensions=['variant','connections','inflight_limit','pipeline_batch','workload','target_commands_s','payload_bytes','diagnostic','allocator']
    grouped={}
    for row in rows:grouped.setdefault(tuple(row[d] for d in dimensions),[]).append(row)
    medians=[]
    for key,runs in grouped.items():
        median=dict(zip(dimensions,key));median['repetitions']=len(runs)
        median['limited_runs']=sum(r['status']=='LIMITED' for r in runs)
        for field in rows[0]:
            values=[r[field] for r in runs if isinstance(r[field],(int,float)) and not isinstance(r[field],bool)]
            if field not in dimensions and values:median[field]=statistics.median(values)
        medians.append(median)
    fields=list(dict.fromkeys(k for r in medians for k in r))
    with (args.results/'medians.csv').open('w',newline='') as output:
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader();writer.writerows(medians)
    unique={r['id']:r for r in rows}
    print(f"Verified completed runs: {len(unique)}; limited: {sum(r['status']=='LIMITED' for r in unique.values())}")
    print('Per-run measurements:',args.results/'per_run.csv')
    print('Medians by identical configuration and payload size:',args.results/'medians.csv')
    print('Keep latency histograms for pooled percentiles; medians of run percentiles are a separate statistic.')

if __name__=='__main__':main()
