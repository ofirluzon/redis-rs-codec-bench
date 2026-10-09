#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ['baseline','patched-off','patched-on']
PROFILES = [('small',50000),('rare',2000),('near',2000),('large',50),('large',200)]

def cases(connections, duration, repetitions, warmup=30, idle=30, fixed_total=True):
    result=[]
    for rep in range(repetitions):
        for workload,rate in PROFILES:
            for count in connections:
                total = {'small':128,'rare':64,'near':64,'large':16}[workload]
                per = max(1,total//count)
                for version in VERSIONS[rep%3:]+VERSIONS[:rep%3]:
                    result.append({'id':f'{workload}-{rate}-c{count}-i{per}-r{rep+1}-{version}',
                        'variant':version,'workload':workload,'connections':count,'per_connection_inflight':per,
                        'rate':rate,'duration_s':duration,'warmup_s':warmup,'idle_s':idle,'workers':4,
                        'pipeline_batch':1,'seed':20261010+rep,'spike_mode':'random'})
    return result

def write(name, rows):
    path=ROOT/'plans'/f'{name}.json'
    path.write_text(json.dumps(rows,indent=2))
    seconds=sum(r['duration_s']+r['warmup_s']+r['idle_s']+2 for r in rows)
    print(f'{name}: {len(rows)} runs, {seconds/3600:.2f} hours excluding setup/drain time')

def main():
    write('screening',cases([1,2,4,8,16],120,1,warmup=10,idle=10))
    write('main',cases([1,16,100],300,3))
    # A separate small-payload capacity pilot: a fixed total of 16 may not reach 50k/s at network RTT.
    write('small-capacity-pilot',[r for r in cases([1,16],30,1,warmup=5,idle=0,fixed_total=False) if r['workload']=='small'])
    diagnostic=[]
    for workload,rate in [('rare',2000),('large',200),('single-spike',50),('burst',50)]:
        for inflight in [1,4,16,64]:
            for version in VERSIONS:
                diagnostic.append({'id':f'diagnostic-{workload}-{rate}-c1-i{inflight}-{version}',
                    'variant':version,'workload':workload,'connections':1,'per_connection_inflight':inflight,
                    'rate':rate,'duration_s':180,'warmup_s':0 if workload=='single-spike' else 10,'idle_s':60,'workers':4,
                    'pipeline_batch':1,'seed':20261010,'spike_mode':'random'})
    write('diagnostic',diagnostic)
    pipelines=[]
    for count in [1,4,16]:
        for batch in [4,16]:
            for workload,rate in [('rare',2000),('large',200)]:
                # Fixed total outstanding=64 across counts and both pipeline batch sizes.
                for version in VERSIONS:
                    pipelines.append({'id':f'pipeline-{workload}-{rate}-c{count}-b{batch}-{version}',
                        'variant':version,'workload':workload,'connections':count,'per_connection_inflight':max(batch,64//count),
                        'rate':rate,'duration_s':180,'warmup_s':10,'idle_s':30,'workers':4,
                        'pipeline_batch':batch,'seed':20261010,'spike_mode':'random'})
    # c16/b16 requires total 256; exclude so the pipeline comparison stays at 64 outstanding.
    pipelines=[r for r in pipelines if r['connections']*r['per_connection_inflight']==64]
    write('pipeline-screening',pipelines)
    # Extended confirmation is selected from screening; these are the known problematic cases.
    write('confirmation',[r for r in cases([1,100],900,3,warmup=30,idle=60)
                          if (r['connections']==1 and (r['workload']=='rare' or (r['workload']=='large' and r['rate']==200)))
                          or (r['connections']==100 and r['workload']=='rare')])

if __name__=='__main__':main()
