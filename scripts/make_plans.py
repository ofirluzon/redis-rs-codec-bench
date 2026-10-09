#!/usr/bin/env python3
"""Generate frozen plans and a readable inventory from matrix.json. Never runs traffic."""
import argparse
import json
from pathlib import Path
from validation import normalize_plan, VARIANTS
ROOT = Path(__file__).resolve().parents[1]

def generate(matrix, stage, overrides=None):
    defaults = matrix['defaults']
    settings = dict(defaults, **matrix['stages'][stage])
    settings.update(overrides or {})
    if settings.get('mode','scored') not in ['scored','diagnostic','jemalloc']: raise ValueError('Invalid stage build mode')
    if not settings.get('variants') or any(v not in VARIANTS for v in settings['variants']): raise ValueError('Invalid variants')
    rows = []
    groups = settings.get('groups', [{}])
    for group in groups:
        cfg = dict(settings, **group)
        # CLI overrides apply to every group, including confirmation selections.
        cfg.update(overrides or {})
        for rep in range(cfg['repetitions']):
            versions = cfg['variants']
            offset = rep % len(versions)
            versions = versions[offset:] + versions[:offset]
            for profile in cfg['profiles']:
                p = matrix['profiles'][profile]
                for count in cfg['connections']:
                    budget = cfg.get('budget', p['budget'])
                    per_values = cfg.get('inflight', [max(1, budget // count)])
                    for per in per_values:
                        for batch in cfg.get('pipeline_batches', [1]):
                            if batch > per: continue
                            # Preserve exactly the selected aggregate budget for pipeline controls.
                            if 'pipeline_batches' in cfg and count * per != budget: continue
                            for version in versions:
                                case = dict(id=f'{stage}-{profile}-c{count}-i{per}-b{batch}-r{rep+1}-{version}',
                                            variant=version, workload=p['workload'], connections=count,
                                            per_connection_inflight=per, rate=p['rate'], duration_s=cfg['duration_s'],
                                            warmup_s=0 if p['workload']=='single-spike' else cfg['warmup_s'],
                                            idle_s=cfg['idle_s'], workers=cfg['workers'], pipeline_batch=batch,
                                            seed=cfg['seed']+rep, spike_mode=cfg['spike_mode'])
                                rows.append(case)
    return normalize_plan(rows), settings.get('mode', 'scored')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT/'matrix.json')
    p.add_argument('--stage', action='append', help='Select stage(s); default generates all plans, never launches')
    p.add_argument('--output', type=Path, default=ROOT/'plans')
    p.add_argument('--list', action='store_true', help='Print inventory without writing plans')
    p.add_argument('--connections', help='Comma-separated physical counts')
    p.add_argument('--profiles', help='Comma-separated profile names in matrix.json')
    p.add_argument('--variants', help='Comma-separated baseline,patched-off,patched-on')
    for flag in ['duration-s','repetitions','workers']: p.add_argument('--'+flag, type=int)
    args = p.parse_args()
    matrix = json.loads(args.config.read_text())
    overrides = {}
    for name in ['connections','profiles','variants']:
        value = getattr(args,name)
        if value: overrides[name] = [int(x) for x in value.split(',')] if name=='connections' else value.split(',')
    for name in ['duration_s','repetitions','workers']:
        value = getattr(args,name)
        if value is not None:
            if value <= 0: p.error(name+' must be positive')
            overrides[name] = value
    if any(v not in VARIANTS for v in overrides.get('variants', [])): p.error('Unknown variant')
    table = ['# Generated matrix inventory', '', 'All rates are aggregate commands/s. Run counts and minimum hours include every selected version and repetition, plus warm-up, idle and the two-second post-drop observation; they exclude setup and drain.', '',
             '| Stage | Build mode | Runs | Measured seconds | Connections | Total in flight | Pipeline batch | Repeats | Minimum hours |',
             '| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: |']
    for stage in args.stage or matrix['stages']:
        try: rows, mode = generate(matrix, stage, overrides)
        except (KeyError, ValueError, TypeError) as e: p.error(str(e))
        unique = lambda name: ','.join(str(x) for x in sorted({r[name] for r in rows}))
        seconds = sum(r['duration_s']+r['warmup_s']+r['idle_s']+2 for r in rows)
        total = ','.join(str(x) for x in sorted({r['connections']*r['per_connection_inflight'] for r in rows}))
        reps = (overrides.get('repetitions') or matrix['stages'][stage]['repetitions'])
        table.append(f'| {stage} | {mode} | {len(rows)} | {unique("duration_s")} | {unique("connections")} | {total} | {unique("pipeline_batch")} | {reps} | {seconds/3600:.2f} |')
        if not args.list:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output/(stage+'.json')).write_text(json.dumps(dict(schema=1,mode=mode,cases=rows),indent=2)+'\n')
    text = '\n'.join(table)+'\n'
    print(text)
    if not args.list: (args.output/'README.md').write_text(text)

if __name__=='__main__': main()
