"""Human reports of validated runs. Aggregates never replace individual observations."""
import re
import statistics

CONFIG_FIELDS = ['workload','connections','inflight_limit','pipeline_batch','target_commands_s',
                 'duration_s','warmup_s','idle_s','workers','spike_mode']

def config_key(row): return tuple(row[k] for k in CONFIG_FIELDS)

def planned_row(cfg):
    return dict(cfg,inflight_limit=cfg['connections']*cfg['per_connection_inflight'],target_commands_s=cfg['rate'])

def rep_number(run_id):
    match=re.search(r'(?:^|-)r([1-9][0-9]*)(?:-|$)',run_id)
    return int(match[1]) if match else None

def cell(runs, expected, field, digits=2, validated_ids=None):
    parts=[];values=[]
    for label,run_id in expected:
        row=runs.get(run_id)
        if row is None:
            reason='no observations' if run_id in (validated_ids or set()) else 'missing run'
            parts.append(f'{label}: {reason}')
        elif row.get(field) is None:
            parts.append(f'{label}: —')
        else:
            value=row[field];values.append(value)
            parts.append(f'{label}: {value:,.{digits}f}')
    if values:
        title='Mean' if len(values)==len(expected) else f'Mean (n={len(values)})'
        parts.append(f'**{title}: {statistics.mean(values):,.{digits}f}**')
    else: parts.append('**Mean: —**')
    return '<br>'.join(parts)

def expected_runs(rows, plan_cases):
    if plan_cases:
        cases=plan_cases
    else:
        # Preserve holes in generated repetition IDs, including an absent middle run.
        by_id={r['id']:r for r in rows}
        cases=list(by_id.values())
        numbered=[rep_number(r['id']) for r in cases]
        if cases and all(numbered):
            template=cases[0]['id']
            cases=[dict(id=re.sub(r'(?<=-)r[1-9][0-9]*(?=-|$)',f'r{n}',template)) for n in range(1,max(numbered)+1)]
    return [(f'r{rep_number(c["id"]) or i+1}',c['id']) for i,c in enumerate(cases)]

def markdown_report(rows, completed, planned, plan=None, scope='aggregate'):
    selected=[r for r in rows if r['group_scope']==scope]
    state='COMPLETE' if completed==planned else 'INCOMPLETE'
    lines=[f'# Codec benchmark report — {state}', '', f'Validated runs: **{completed}/{planned}**.', '',
           'Each numeric cell lists the individual repetitions followed by their **arithmetic mean**. '
           'Means of run percentiles are not pooled percentiles. Missing observations are excluded from means; '
           '`n` shows the contributing count when it differs from the planned repetitions.', '',
           'CPU and RSS belong to the whole client process and repeat on every latency-group row; '
           '**do not sum them across groups**. All latency and CPU/command values are µs; RSS is MiB. '
           'Steady RSS is the median of the last min(120, duration) seconds; peak here is the sampled submission-window peak. '
           'See [metric reference](kit/docs/METRICS.md) for other peaks, windows and diagnostic counters. '
           'The reference is retained in the result kit snapshot.', '',
           '`LIMITED` means achieved rate below 95% of target. `PROBE` is a deliberate spike/burst observation, '
           'not a sustained-throughput success. `—` means unavailable or unreportable, including p99.9 when too few observations or equal to max.', '']
    configs={}
    for row in selected:
        key=(config_key(row),row['diagnostic'],row['allocator'])
        configs.setdefault(key,[]).append(row)
    validated_ids={r['id'] for r in rows}
    columns=[('Commands (group)','group_commands',0),('CPU s (whole run)','total_cpu_s',3),
             ('CPU µs/command (whole run)','cpu_us_per_command',2),('Steady RSS MiB','steady_rss_mib',2),
             ('Peak RSS MiB','sampled_peak_rss_mib',2),('Latency mean µs','response_mean_us',2),
             ('p50 µs','response_p50_us',2),('p99 µs','response_p99_us',2),('p99.9 µs','response_p999_us',2)]
    for (key,diagnostic,allocator),group_rows in configs.items():
        cfg=dict(zip(CONFIG_FIELDS,key))
        lines += [f'## {cfg["workload"]} · {cfg["connections"]} connection(s) · target {cfg["target_commands_s"]:,}/s', '',
                  f'In flight: {cfg["inflight_limit"]} commands; pipeline batch: {cfg["pipeline_batch"]}; '
                  f'workers: {cfg["workers"]}; measured/warm-up/idle: {cfg["duration_s"]}/{cfg["warmup_s"]}/{cfg["idle_s"]} s; '
                  f'spikes: {cfg["spike_mode"]}; allocator: {allocator}; diagnostics: {diagnostic}.', '']
        matches=[c for c in (plan or []) if config_key(planned_row(c))==key]
        builds={}
        for row in group_rows:
            builds.setdefault((row['variant'],row['source_commit'],row['binary_sha256']),[]).append(row)
        # Show a planned variant even when none of its runs have completed.
        for c in matches:
            if not any(b[0]==c['variant'] for b in builds): builds[(c['variant'],'pending','pending')]=[]
        groups=list(dict.fromkeys(r['group'] for r in group_rows))
        if scope=='aggregate' and cfg['workload'] in ['rare','burst','single-spike']:
            groups=list(dict.fromkeys(['all','ordinary','spike']+groups))
        lines += ['| Group | Version | '+' | '.join(c[0] for c in columns)+' |',
                  '| --- | --- | '+' | '.join('---:' for c in columns)+' |']
        for name in groups:
            for (variant,commit,binary),version_rows in builds.items():
                expected=expected_runs(version_rows,[c for c in matches if c['variant']==variant])
                runs={r['id']:r for r in version_rows if r['group']==name}
                title=f'{variant} ({len(runs)}/{len(expected)})'
                lines.append('| '+name+' | '+title+' | '+' | '.join(cell(runs,expected,field,digits,validated_ids) for _,field,digits in columns)+' |')
        lines += ['', '### Run status and build identity', '',
                  '| Version | Repetition | Total commands | Achieved commands/s | Status | Errors | Source / binary SHA-256 |',
                  '| --- | --- | ---: | ---: | --- | ---: | --- |']
        for (variant,commit,binary),version_rows in builds.items():
            expected=expected_runs(version_rows,[c for c in matches if c['variant']==variant])
            by_id={r['id']:r for r in version_rows}
            for label,run_id in expected:
                row=by_id.get(run_id)
                if row:
                    lines.append(f'| {variant} | {label} | {row["total_commands"]:,} | {row["achieved_commands_s"]:,.2f} | {row["status"]} | {row["errors"]} | `{commit}` / `{binary}` |')
                else: lines.append(f'| {variant} | {label} | — | — | missing run | — | — |')
        lines.append('')
    shown={key for key,_,_ in configs}
    pending=[c['id'] for c in (plan or []) if config_key(planned_row(c)) not in shown]
    if pending:
        lines += ['## Configurations with no completed observations', '',
                  'These planned cases have no table yet:', '']
        lines.extend('- `'+run_id+'`' for run_id in pending)
        lines.append('')
    return '\n'.join(lines)+'\n'
