"""Reproduce the historical 1/16/100 report from the October ARM archive."""
import csv, hashlib, html, json, math, pathlib, re, statistics
from report_data import interpretation, artifact_root, load_stage, SCRIPT_DIR

ROOT = artifact_root()
data = load_stage(ROOT, 'main')
rows, means, medians, allrows, configs, extra, builds = (data[k] for k in ['rows','means','medians','allrows','configs','extra','builds'])
config = lambda r: (r['workload'], int(float(r['target_commands_s'])), int(r['connections']))
variants = ['baseline','patched-off','patched-on']
titles={'small':'Small replies','rare':'Rare large replies','near':'Near threshold','large':'Large replies'}
def cname(c):return f'{titles[c[0]]} · {c[1]:,}/s · {c[2]} '+('connection' if c[2]==1 else 'connections')
def fmt(v):
    if v is None or v=='':return '—'
    try:return f'{float(v):,.3f}'.rstrip('0').rstrip('.')
    except ValueError:return html.escape(str(v))
meanall=[m for m in means if m['group_scope']=='aggregate' and m['group']=='all']
def summary_table(keys):
    h='<div class="scroll"><table><thead><tr><th>Workload / target / connections</th><th>Version</th>'+''.join('<th>'+html.escape(label)+'</th>' for k,label in keys)+'</tr></thead><tbody>'
    for c in configs:
        for v in variants:
            m=next(m for m in meanall if config(m)==c and m['variant']==v)
            h+='<tr><td>'+html.escape(cname(c))+'</td><td>'+v+'</td>'+''.join('<td>'+fmt(m[k])+'</td>' for k,label in keys)+'</tr>'
    return h+'</tbody></table></div>'

body=interpretation(ROOT, 'build_main_report-25')
body+=summary_table([('cpu_us_per_command','CPU µs/command'),('steady_rss_mib','Steady RSS MiB'),('sampled_peak_rss_mib','Sampled peak MiB'),('response_mean_us','Response mean µs'),('response_p99_us','Response p99 µs')])
body+=interpretation(ROOT, 'build_main_report-42')
for v,b in builds.items():
    body+=f'<p>{v} source <span class="hash">{b["commit"]}</span><br>Binary SHA-256 <span class="hash">{b["binary_sha256"]}</span></p>'

categories={
 'CPU and rates':['target_commands_s','achieved_commands_s','total_commands','offered','not_submitted','capacity_skipped','deadline_skipped','errors','user_cpu_s','system_cpu_s','total_cpu_s','user_cpu_us_per_command','system_cpu_us_per_command','cpu_us_per_command','drain_s'],
 'Memory':['average_rss_mib','steady_rss_mib','sampled_peak_rss_mib','sampled_peak_including_drain_mib','process_peak_including_warmup_mib','idle_final_rss_mib','after_drop_rss_mib','rss_slope_mib_per_min','steady_rss_slope_mib_per_min','steady_rss_min_mib','steady_rss_max_mib'],
 'Latency':[k for k in rows[0] if k.startswith(('response_','scheduled_to_response_','scheduling_')) and not k.endswith('reportable')]+['group_commands'],
 'Occupancy':['inflight_limit','average_inflight_commands','peak_inflight_commands','average_inflight_payload_mib','peak_inflight_payload_mib','large_connections_covered','large_commands_min_per_connection'],
 'Host and network':['host_busy_pct','host_sample_span_s']+list(next(iter(extra.values())))+[k for k in allrows[0] if k.startswith(('net_','tcp_','host_cpu_'))],
 'Endpoint INFO':['server_info_samples','server_info_sample_span_s']+[k for k in allrows[0] if k.startswith('endpoint_')]
}
data=dict(rows=rows,means=means,medians=medians,configs=[list(c) for c in configs],names=[cname(c) for c in configs],categories=categories)
body+='<script id="data" type="application/json">'+json.dumps(data).replace('<','\\u003c')+'</script>'
body+='<script>\n'+(SCRIPT_DIR/'detail.js').read_text()+'</script></main></html>'
(ROOT/'main-report.html').write_text(body)
print('Validated 135 runs, 45 matched three-repetition configurations, key arithmetic means and retained kit hashes.')
print('Additional host extremes:', {k: (min(e[k] for e in extra.values()),max(e[k] for e in extra.values())) for k in next(iter(extra.values()))})
print('Report:', ROOT/'main-report.html')
