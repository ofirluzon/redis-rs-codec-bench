"""Shared preflight and immutable-result checks; no endpoint data is stored here."""
import hashlib
import json
import math
import re

DEFAULTS = dict(warmup_s=30, idle_s=30, workers=4, pipeline_batch=1, seed=0, spike_mode='random')
REQUIRED = {'id', 'variant', 'workload', 'connections', 'per_connection_inflight', 'rate', 'duration_s'}
VARIANTS = ['baseline', 'patched-off', 'patched-on']
WORKLOADS = ['small', 'rare', 'near', 'large', 'burst', 'single-spike']

def normalize_plan(rows):
    if not isinstance(rows, list) or not rows: raise ValueError('Plan must be a nonempty list')
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or REQUIRED - row.keys() or row.keys() - REQUIRED - DEFAULTS.keys():
            raise ValueError('Missing or unknown case fields')
        c = dict(DEFAULTS, **row)
        if not isinstance(c['id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,180}', c['id']):
            raise ValueError('Case ID must be a safe directory name')
        if c['variant'] not in VARIANTS or c['workload'] not in WORKLOADS or c['spike_mode'] not in ['random', 'synchronized']:
            raise ValueError('Unknown variant, workload or spike mode')
        for name in ['connections', 'per_connection_inflight', 'rate', 'duration_s', 'warmup_s', 'idle_s', 'workers', 'pipeline_batch', 'seed']:
            if type(c[name]) is not int or c[name] < (0 if name in ['warmup_s', 'idle_s', 'seed'] else 1):
                raise ValueError('Invalid integer field: ' + name)
            if c[name] > 2**64 - 1: raise ValueError('Integer overflow: ' + name)
        if c['per_connection_inflight'] > 2**32 - 1 or c['pipeline_batch'] > c['per_connection_inflight']:
            raise ValueError('Batch must fit per-connection concurrency')
        if max(c['duration_s'], c['warmup_s'], 20) * c['rate'] > 2**64 - 1:
            raise ValueError('Command count overflow')
        if c['connections'] * c['per_connection_inflight'] > 2**64 - 1:
            raise ValueError('Concurrency overflow')
        if c['workload'] == 'single-spike' and (c['duration_s'] <= 5 or c['warmup_s'] != 0 or c['pipeline_batch'] != 1 or c['connections'] != 1):
            raise ValueError('Single-spike needs one connection, no warmup, batch 1 and >5 seconds')
        normalized.append(c)
    if len({c['id'] for c in normalized}) != len(normalized): raise ValueError('Duplicate case IDs')
    return normalized

def validate_summary(config, summary, build):
    errors = []
    if summary.get('schema')!=2: errors.append('unsupported summary schema; use fresh results')
    if summary.get('config') != config: errors.append('summary configuration mismatch')
    if summary.get('errors') != 0 or summary.get('completed', 0) <= 0: errors.append('errors or no completed commands')
    if summary.get('build_commit') != build.get('commit'): errors.append('binary/source mismatch')
    features = build.get('features', [])
    for field, feature in [('patched_api', 'patched'), ('allocation_diagnostics', 'alloc-diagnostics'), ('codec_diagnostics', 'codec-diagnostics')]:
        if summary.get(field) != (feature in features): errors.append('build mode mismatch: ' + field)
    if summary.get('allocator') != ('jemalloc' if 'jemalloc' in features else 'system'): errors.append('allocator mismatch')
    if summary.get('submit_window_s') != config['duration_s']: errors.append('wrong measured duration')
    if summary.get('measure_end_s', 0) - summary.get('measure_start_s', 0) < config['duration_s']: errors.append('short measurement window')
    groups = summary.get('groups', {})
    if sum(g['commands'] for g in groups.values()) != summary.get('completed'): errors.append('histogram command count mismatch')
    if summary.get('aggregate_groups',{}).get('all',{}).get('commands')!=summary.get('completed'): errors.append('aggregate histogram count mismatch')
    if sum(g['bytes'] for g in groups.values())!=summary.get('payload_bytes_each_direction'): errors.append('payload byte accounting mismatch')
    if summary.get('capacity_skipped',0)+summary.get('deadline_skipped',0)!=summary.get('not_submitted'): errors.append('skip accounting mismatch')
    for g in list(groups.values()) + list(summary.get('aggregate_groups', {}).values()):
        for name in ['response', 'scheduled_to_response', 'scheduling']:
            h = g[name]
            if h['count'] != g['commands']: errors.append('histogram sample count mismatch')
            if not (0 <= h['p50_us'] <= h['p99_us'] <= h['p999_us'] <= h['max_us']): errors.append('invalid latency quantiles')
    if summary.get('peak_inflight_commands', 0) > config['connections'] * config['per_connection_inflight']: errors.append('concurrency limit exceeded')
    if summary.get('not_submitted', 0) + summary.get('completed', 0) != summary.get('offered', 0): errors.append('offered/completed accounting mismatch')
    expected = config['rate'] * (5 if config['workload'] == 'single-spike' else config['duration_s']) + (config['workload'] == 'single-spike')
    if summary.get('offered') != expected: errors.append('wrong offered count')
    rate = summary['completed'] / config['duration_s']
    if not math.isclose(summary.get('achieved_commands_s', -1), rate, rel_tol=1e-9): errors.append('wrong achieved rate')
    status = 'PROBE' if config['workload'] in ['single-spike', 'burst'] else 'OK' if rate >= .95 * config['rate'] else 'LIMITED'
    if summary.get('status') != status: errors.append('wrong status')
    if config['workload'] == 'single-spike' and sum(summary['large_commands_per_connection']) != 1: errors.append('single spike was not completed')
    for key in ['user_cpu_s', 'system_cpu_s', 'cpu_us_per_command', 'drain_s']:
        value = summary.get(key)
        if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0: errors.append('invalid metric: ' + key)
    cpu = (summary['user_cpu_s'] + summary['system_cpu_s']) * 1e6 / max(1, summary['completed'])
    if not math.isclose(summary.get('cpu_us_per_command', -1), cpu, rel_tol=1e-9): errors.append('wrong CPU per command')
    return errors

def checksums(attempt):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(attempt.iterdir()) if p.is_file()}

def validate_artifacts(attempt, summary, linux):
    samples = [json.loads(line) for line in (attempt / 'samples.jsonl').read_text().splitlines()]
    start, end = summary['measure_start_s'], summary['measure_start_s'] + summary['config']['duration_s']
    measured = [s for s in samples if s['phase'] == 1 and start <= s['elapsed_s'] <= end]
    if not measured: raise ValueError('Missing measured telemetry')
    if linux and any(s.get('rss_bytes') is None for s in measured): raise ValueError('Missing Linux RSS')
    times = [s['elapsed_s'] for s in measured]
    if max([times[0] - start, end - times[-1]] + [b - a for a, b in zip(times, times[1:])]) > 2:
        raise ValueError('RSS sampling gap exceeds two seconds')
    if summary['config']['idle_s'] and not any(s['phase'] == 2 for s in samples): raise ValueError('Missing idle observation')
    if not any(s['phase'] == 3 for s in samples): raise ValueError('Missing connection-drop observation')
    for size in list(summary['groups']) + list(summary.get('aggregate_groups', {})):
        for name in ['response', 'scheduled', 'scheduling']:
            if not (attempt / f'latency-{size}-{name}.hdr').stat().st_size: raise ValueError('Empty histogram')
    host = [json.loads(line) for line in (attempt / 'host.jsonl').read_text().splitlines()]
    if not host: raise ValueError('Missing host telemetry')
    if linux and any(not h.get('proc_stat') or not h.get('net_dev') or not h.get('net_snmp') for h in host):
        raise ValueError('Missing Linux host counters')
    if (attempt / 'errors.jsonl').read_text().strip(): raise ValueError('Nonempty command error log')
    return samples

def load_done(parent, config, build, linux):
    done = json.loads((parent / 'DONE.json').read_text())
    if not re.fullmatch(r'attempt-[0-9]+', done['attempt']): raise ValueError('Invalid attempt path')
    attempt = parent / done['attempt']
    if done['build_sha256'] != build['binary_sha256']: raise ValueError('Completed run used a different build')
    if done.get('files_sha256') != checksums(attempt): raise ValueError('Completed artifacts changed or are incomplete')
    summary = json.loads((attempt / 'summary.json').read_text())
    failures = validate_summary(config, summary, build)
    if failures: raise ValueError(', '.join(failures))
    return summary, validate_artifacts(attempt, summary, linux), attempt
