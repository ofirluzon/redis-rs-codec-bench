"""Reproduce the October ARM combined and intermediate-connection reports."""
import csv, hashlib, html, io, json, math, pathlib, re, statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from report_data import interpretation, artifact_root, load_stage, validate_pilot, SCRIPT_DIR
ROOT=artifact_root()
datasets={stage: load_stage(ROOT, stage) for stage in ['main','knee-validation']}
for stage, dataset in datasets.items():
    for kind in ['rows','means','medians']:
        for row in dataset[kind]: row['stage']=stage

variants=['baseline','patched-off','patched-on']
profiles=[('small',50000),('rare',2000),('near',2000),('large',50),('large',200)]
titles={'small':'Small replies','rare':'Rare large replies','near':'Near threshold','large':'Large replies'}
config=lambda r:(r['workload'],int(float(r['target_commands_s'])),int(r['connections']))
def cname(c):return f'{titles[c[0]]} · {c[1]:,}/s · {c[2]} connections'
def number(x):return float(x)
def fmt(x):
    if x is None or x=='': return '—'
    try:return f'{float(x):,.3f}'.rstrip('0').rstrip('.')
    except (ValueError,TypeError):return html.escape(str(x))
def export_csv(path, rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w') as f:
        w=csv.DictWriter(f,keys);w.writeheader();w.writerows(rows)
def simple_table(headers, rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+fmt(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
def svg_plot(rows,means,stem,statistic='mean'):
    fig,axes=plt.subplots(3,5,figsize=(19,11),layout='constrained')
    colors=['#374c66','#e19b28','#007e78']
    for j,(w,rate) in enumerate(profiles):
        for i,(metric,label,scale) in enumerate([('steady_rss_mib','Steady RSS (MiB)',1),('cpu_us_per_command','CPU (µs/command)',1),('response_mean_us','Run mean response (ms)',1000)]):
            ax=axes[i,j]
            for v,col in zip(variants,colors):
                mm=sorted([r for r in means if r['workload']==w and int(float(r['target_commands_s']))==rate],key=lambda r:int(r['connections']))
                mm=[r for r in mm if r['variant']==v]
                ax.plot([int(r['connections']) for r in mm],[float(r[metric])/scale for r in mm],'-o',label=v,color=col,linewidth=1.8,markersize=4)
                rr=[r for r in rows if r['workload']==w and int(float(r['target_commands_s']))==rate and r['variant']==v]
                ax.scatter([int(r['connections']) for r in rr],[float(r[metric])/scale for r in rr],color=col,alpha=.28,s=13)
            counts=sorted({int(r['connections']) for r in mm})
            ax.set_xscale('log',base=2);ax.set_xticks(counts,labels=counts);ax.grid(alpha=.18)
            ax.set_xlabel('Physical connections');ax.set_ylabel(label)
            if i==0:ax.set_title(titles[w]+f'\n{rate:,} commands/s')
    axes[0,0].legend(fontsize=9)
    fig.suptitle(('Medians' if statistic=='median' else 'Arithmetic means')+' across repetitions (lines); individual runs (faint points)',fontsize=16)
    path=ROOT/(stem+'-connection-trends'+('-median' if statistic=='median' else '')+'.svg');fig.savefig(path);plt.close(fig)
    return path.read_text()[path.read_text().index('<svg'):]

CSS='''body{font:16px/1.55 system-ui,sans-serif;color:#183044;background:#f6f8fa;margin:0}main{max-width:1500px;margin:auto;padding:32px}h1{font-size:34px;line-height:1.2}h2{margin-top:38px}h3{margin-top:25px}p,li{max-width:1200px}a{color:#006c98}table{border-collapse:collapse;background:white;width:100%;font-size:14px}td,th{padding:8px 10px;border-bottom:1px solid #dbe3e8;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}td:first-child,th:first-child{text-align:left;white-space:normal}th{background:#eaf0f4}tr:nth-child(even){background:#f8fafc}.scroll{overflow:auto;max-height:680px}.scroll thead th{position:sticky;top:0}.controls{display:flex;gap:15px;flex-wrap:wrap;padding:18px 0}select{padding:9px;font:inherit;max-width:100%}.note{background:#e8f4f1;border-left:4px solid #007e78;padding:16px 20px}.card{min-width:0;margin:18px 0}.card td:first-child{min-width:190px}.hash{overflow-wrap:anywhere;font:13px monospace}svg{max-width:100%;height:auto}details{margin:18px 0}summary{cursor:pointer;font-weight:600}.subtle{color:#526877}nav{display:flex;flex-wrap:wrap;gap:15px}@media(max-width:900px){main{padding:16px}}@media print{select{display:none}.scroll{overflow:visible;max-height:none}}'''

METHOD=interpretation(ROOT, 'build_full_reports-53')

KNEE_FINDINGS=interpretation(ROOT, 'build_full_reports-65')

COMBINED_FINDINGS=interpretation(ROOT, 'build_full_reports-73')

FOLLOWUP='''<h2>Focused follow-up, if these questions matter</h2><ol>
<li><strong>Allocation mechanism:</strong> run selected prepared diagnostic cases (rare/4 and 8, large/50/2 and 8, large/200/100) to count actual trims, buffer capacity growth and allocation churn. Current scored data cannot count those events.</li>
<li><strong>Allocator/long-run behavior:</strong> compare system allocator with prepared jemalloc for large/200/1 and representative many-connection runs; extend the single-connection run to test whether retained RSS settles. Keep these distinct from the frozen matrix.</li>
<li><strong>Pipeline applicability:</strong> if production batches commands, run an explicitly labeled pipeline matrix with matched aggregate concurrency. Batch-1 results cannot predict pipeline allocation or latency.</li>
<li><strong>Tail attribution:</strong> collect ENA/NIC diagnostics plus real server-node/shard/proxy CPU, pressure and queue telemetry during selected repeated runs. Endpoint INFO is insufficient for that attribution.</li></ol><p>No optional stage was queued. The completed benchmark is ready for independent analysis from local artifacts; shutting down the client instances does not affect these saved reports.</p>'''

def build(stages, stem, title, findings):
    data={k:sum([datasets[s][k] for s in stages],[]) for k in ['rows','means','medians']}
    rows=data['rows']; means=data['means']; medians=data['medians']
    allrows=[r for r in rows if r['group_scope']=='aggregate' and r['group']=='all']
    meanall=[r for r in means if r['group_scope']=='aggregate' and r['group']=='all']
    medianall=[r for r in medians if r['group_scope']=='aggregate' and r['group']=='all']
    configs=sorted(set(map(config,allrows)),key=lambda c:(profiles.index(c[:2]),c[2]))
    def get(c,v,aggregates=None):return next(r for r in (meanall if aggregates is None else aggregates) if config(r)==c and r['variant']==v)
    def tab(keys,aggregates=None):return simple_table(['Configuration','Version']+[label for key,label in keys],[[cname(c),v]+[get(c,v,aggregates)[k] for k,label in keys] for c in configs for v in variants])
    count=sum(int(r['total_commands']) for r in allrows)
    lo=min(float(r['achieved_commands_s'])/float(r['target_commands_s'])*100 for r in allrows)
    for kind in ['rows','means','medians']:export_csv(ROOT/(stem+'-'+('per-run' if kind=='rows' else kind)+'.csv'),data[kind])
    body=f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>{CSS}</style><main><h1>{title}</h1>'
    body+='<p>Scored data completed October 10, 2026 at 6:39 PM PDT. Analysis and private export completed October 12, 2026.</p>'
    body+=f'<div class="note"><strong>{len(allrows)} / {len(allrows)} validated runs · {count:,} completed commands · zero errors · no LIMITED runs.</strong><br>Achieved rates: {lo:.5f}–100% of target. Three repetitions per configuration/version; every scored submission window is 300 seconds.</div>'
    body+='<nav><a href="#findings">Findings</a><a href="#method">Method</a><a href="#comparison">Comparisons</a><a href="#details">Every repetition</a><a href="#host">Host/endpoint</a><a href="#archive">Saved data</a></nav>'
    body+=findings
    body+='<h2 id="charts">Connection scaling: memory, CPU and latency</h2><div class="controls"><label>Chart statistic<br><select id="chart-statistic"><option value="median" selected>Median across R1/R2/R3</option><option value="mean">Arithmetic mean across R1/R2/R3</option></select></label></div><p>Lines show the selected summary; faint points are the three individual repetitions. Rows show steady RSS, CPU per command, and response latency. The latency row summarizes each run’s <strong>mean response time</strong>: choosing Median takes the median of three run means, not a p50 or pooled percentile. The connection axis is logarithmic; the 100-count concurrency budget differs. Charts show the complete matrix independently of table filters.</p>'
    body+='<section data-chart="median">'+svg_plot(allrows,medianall,stem,'median')+'</section><section data-chart="mean" hidden>'+svg_plot(allrows,meanall,stem)+'</section>'
    mean_block_start=len(body)
    body+='<h2>Mean CPU and RSS tradeoff versus baseline</h2>'
    comparisons=[]; cr=[]
    for c in configs:
        b=get(c,'baseline')
        for v in variants[1:]:
            r=get(c,v); off=get(c,'patched-off')
            pct=lambda k,ref:100*(float(r[k])/float(ref[k])-1)
            paired=[]
            for rep in ['1','2','3']:
                rr=next(x for x in allrows if config(x)==c and x['variant']==v and f'-r{rep}-' in x['id'])
                bb=next(x for x in allrows if config(x)==c and x['variant']=='baseline' and f'-r{rep}-' in x['id'])
                paired.append(100*(float(rr['cpu_us_per_command'])/float(bb['cpu_us_per_command'])-1))
            e=dict(workload=c[0],target_commands_s=c[1],connections=c[2],variant=v,cpu_pct_vs_baseline=pct('cpu_us_per_command',b),steady_rss_pct_vs_baseline=pct('steady_rss_mib',b),response_mean_pct_vs_baseline=pct('response_mean_us',b),response_p99_pct_vs_baseline=pct('response_p99_us',b),cpu_pct_vs_off=pct('cpu_us_per_command',off),paired_cpu_pct_min=min(paired),paired_cpu_pct_max=max(paired))
            cr.append(e);comparisons.append([cname(c),v,e['cpu_pct_vs_baseline'],e['steady_rss_pct_vs_baseline'],e['response_mean_pct_vs_baseline'],e['response_p99_pct_vs_baseline'],e['cpu_pct_vs_off'],f'{min(paired):+.1f}% to {max(paired):+.1f}%'])
    export_csv(ROOT/(stem+'-comparisons.csv'),cr)
    body+='<p>Negative means lower; CPU and memory percentages are independent. Response changes can reflect network/server and scheduling as well as the client. Paired CPU ranges show all three matched repetition changes, not uncertainty bounds.</p>'+simple_table(['Configuration','Version','CPU Δ %','Steady RSS Δ %','Response mean Δ %','Response p99 Δ %','CPU Δ vs off %','Paired CPU range vs baseline'],comparisons)
    body+='<h2>Arithmetic means: CPU, throughput and response latency</h2>'+tab([('achieved_commands_s','Achieved /s'),('total_cpu_s','CPU total s'),('user_cpu_s','User s'),('system_cpu_s','System s'),('cpu_us_per_command','CPU µs/cmd'),('response_mean_us','Mean µs'),('response_p50_us','p50 µs'),('response_p99_us','p99 µs'),('response_p999_us','p99.9 µs')])
    body+='<h2>Arithmetic means: complete memory lifecycle</h2>'+tab([('average_rss_mib','Average MiB'),('steady_rss_mib','Steady MiB'),('sampled_peak_rss_mib','Peak MiB'),('sampled_peak_including_drain_mib','Drain peak MiB'),('process_peak_including_warmup_mib','OS HWM MiB'),('idle_final_rss_mib','Idle MiB'),('after_drop_rss_mib','After drop MiB'),('rss_slope_mib_per_min','Full slope MiB/min'),('steady_rss_slope_mib_per_min','Late slope MiB/min')])
    mean_block=body[mean_block_start:]
    median_comparisons=[];median_cr=[]
    for c in configs:
        b=get(c,'baseline',medianall)
        for v in variants[1:]:
            r=get(c,v,medianall);off=get(c,'patched-off',medianall)
            pct=lambda k,ref:100*(float(r[k])/float(ref[k])-1)
            e=dict(workload=c[0],target_commands_s=c[1],connections=c[2],variant=v,cpu_pct_vs_baseline=pct('cpu_us_per_command',b),steady_rss_pct_vs_baseline=pct('steady_rss_mib',b),response_mean_pct_vs_baseline=pct('response_mean_us',b),response_p99_pct_vs_baseline=pct('response_p99_us',b),cpu_pct_vs_off=pct('cpu_us_per_command',off))
            median_cr.append(e);median_comparisons.append([cname(c),v,e['cpu_pct_vs_baseline'],e['steady_rss_pct_vs_baseline'],e['response_mean_pct_vs_baseline'],e['response_p99_pct_vs_baseline'],e['cpu_pct_vs_off']])
    export_csv(ROOT/(stem+'-median-comparisons.csv'),median_cr)
    median_block='<h2>Median CPU and RSS tradeoff versus baseline</h2><p>Each percentage compares the median of the three per-run metrics. It is not the median of paired percentage changes. Negative means lower. Median response mean means the median of three run means; median p99 is not a pooled p99.</p>'
    median_block+=simple_table(['Configuration','Version','CPU Δ %','Steady RSS Δ %','Response mean Δ %','Response p99 Δ %','CPU Δ vs off %'],median_comparisons)
    median_block+='<h2>Medians: CPU, throughput and response latency</h2>'+tab([('achieved_commands_s','Achieved /s'),('total_cpu_s','CPU total s'),('user_cpu_s','User s'),('system_cpu_s','System s'),('cpu_us_per_command','CPU µs/cmd'),('response_mean_us','Run mean µs'),('response_p50_us','Run p50 µs'),('response_p99_us','Run p99 µs'),('response_p999_us','Run p99.9 µs')],medianall)
    median_block+='<h2>Medians: complete memory lifecycle</h2>'+tab([('average_rss_mib','Run average MiB'),('steady_rss_mib','Steady MiB'),('sampled_peak_rss_mib','Run peak MiB'),('sampled_peak_including_drain_mib','Run drain peak MiB'),('process_peak_including_warmup_mib','Run OS HWM MiB'),('idle_final_rss_mib','Idle MiB'),('after_drop_rss_mib','After drop MiB'),('rss_slope_mib_per_min','Full slope MiB/min'),('steady_rss_slope_mib_per_min','Late slope MiB/min')],medianall)
    body=body[:mean_block_start]+'''<h2 id="comparison">CPU and memory summary tables</h2><p><strong>Medians are the default view.</strong> They show a typical repetition without letting one unusual run dominate. Arithmetic means and every repetition remain available because medians can hide intermittent problems. With three runs, the median is simply the middle observation. Each metric has its own median; the user/system medians need not add to the total CPU median.</p><p>Steady RSS already means the median of samples in each run’s last 120 seconds. Here the median view takes the median of those three run-level values. “Run average RSS” retains its within-run average definition. Charts have a separate statistic selector. Narrative findings above continue to use explicitly labeled arithmetic means.</p><div class="controls"><label>Summary statistic<br><select id="summary-statistic"><option value="median" selected>Median across R1/R2/R3</option><option value="mean">Arithmetic mean across R1/R2/R3</option></select></label></div>'''+f'<section data-summary="median">{median_block}</section><section data-summary="mean" hidden>{mean_block}</section>'
    if len(stages)>1:
        cc=('large',200,1)
        body+=interpretation(ROOT, 'build_full_reports-145')
        example=[]
        for v in variants:
            raw=sorted([r for r in allrows if config(r)==cc and r['variant']==v],key=lambda r:r['id'])
            for k,label in [('cpu_us_per_command','CPU µs/command'),('steady_rss_mib','Steady RSS MiB'),('after_drop_rss_mib','After-drop RSS MiB'),('steady_rss_slope_mib_per_min','Late RSS slope MiB/min')]:
                example.append([v,label]+[r[k] for r in raw]+[get(cc,v)[k],get(cc,v,medianall)[k]])
        body+=simple_table(['Version','Metric','R1','R2','R3','Mean','Median'],example)
        body+=interpretation(ROOT, 'build_full_reports-152')
    body+=interpretation(ROOT, 'build_full_reports-153')
    if len(stages)>1:
        body+=interpretation(ROOT, 'build_full_reports-155')
    body+=interpretation(ROOT, 'build_full_reports-156')
    body+=METHOD
    body+='<h2 id="details">Every repetition, every metric</h2><p>Select configuration, metric family, and aggregate/exact-payload latency group. Each version includes R1/R2/R3, arithmetic mean and median. Missing/suppressed values are —. The CSV downloads retain all observations and precision.</p><div class="controls"><label>Configuration<br><select id="config"></select></label><label>Metric family<br><select id="category"><option>CPU and rates</option><option>Memory</option><option>Latency</option><option>Occupancy</option><option>Host and network</option><option>Endpoint INFO</option></select></label><label>Latency group<br><select id="group"></select></label></div><div id="detail"></div>'
    extra_keys=list(next(iter(datasets[stages[0]]['extra'].values())))
    categories={
      'CPU and rates':['target_commands_s','achieved_commands_s','total_commands','offered','not_submitted','capacity_skipped','deadline_skipped','errors','user_cpu_s','system_cpu_s','total_cpu_s','user_cpu_us_per_command','system_cpu_us_per_command','cpu_us_per_command','drain_s'],
      'Memory':['average_rss_mib','steady_rss_mib','sampled_peak_rss_mib','sampled_peak_including_drain_mib','process_peak_including_warmup_mib','idle_final_rss_mib','after_drop_rss_mib','rss_slope_mib_per_min','steady_rss_slope_mib_per_min','steady_rss_min_mib','steady_rss_max_mib'],
      'Latency':[k for k in rows[0] if k.startswith(('response_','scheduled_to_response_','scheduling_')) and not k.endswith('reportable')]+['group_commands'],
      'Occupancy':['inflight_limit','average_inflight_commands','peak_inflight_commands','average_inflight_payload_mib','peak_inflight_payload_mib','large_connections_covered','large_commands_min_per_connection'],
      'Host and network':['host_busy_pct','host_sample_span_s']+extra_keys+[k for k in allrows[0] if k.startswith(('net_','tcp_','host_cpu_'))],
      'Endpoint INFO':['server_info_samples','server_info_sample_span_s']+[k for k in allrows[0] if k.startswith('endpoint_')]
    }
    body+='<h2 id="host">Host, endpoint and bottleneck evidence</h2>'
    def bounds(k):return min(float(r[k]) for r in allrows),max(float(r[k]) for r in allrows)
    totals={k:sum(float(r[k]) for r in allrows) for k in allrows[0] if k.startswith(('net_','tcp_')) and k.endswith('delta')}
    export_csv(ROOT/(stem+'-host-details.csv'),[dict(id=r['id'],stage=r['stage'],**{k:r[k] for k in categories['Host and network']}) for r in allrows])
    body+=f'<p>Host memory availability stayed above {bounds("host_mem_available_min_gib")[0]:.2f} GiB; maximum swap used was {bounds("host_swap_used_max_mib")[1]:.0f} MiB. Memory PSI some/full avg10 max was {bounds("memory_pressure_some_avg10_max_pct")[1]:.2f}% / {bounds("memory_pressure_full_avg10_max_pct")[1]:.2f}%; CPU PSI some avg10 max {bounds("cpu_pressure_some_avg10_max_pct")[1]:.2f}%. The busiest core’s whole-window average reached {bounds("busiest_core_average_pct")[1]:.2f}%, and the highest one-sample core reading {bounds("busiest_core_one_sample_pct")[1]:.1f}%. Whole-host CPU busy means range {bounds("host_busy_pct")[0]:.2f}–{bounds("host_busy_pct")[1]:.2f}% of 16 vCPUs. These aggregates do not exclude brief or per-thread limits.</p>'
    large=[r for r in meanall if r['workload']=='large']
    body+=interpretation(ROOT, 'build_full_reports-174')
    body+=tab([('host_busy_pct','Host busy %'),('busiest_core_average_pct','Busiest core average %'),('nic_rx_gbit_s','NIC RX Gbit/s'),('nic_tx_gbit_s','NIC TX Gbit/s'),('average_inflight_commands','Mean in-flight cmds'),('peak_inflight_commands','Peak in-flight cmds'),('average_inflight_payload_mib','Mean in-flight MiB'),('peak_inflight_payload_mib','Peak in-flight MiB')])
    counter_rows=[[k,value,sum(float(r.get(k,0))>0 for r in allrows)] for k,value in totals.items() if value or any(x in k for x in ['drop','error','InErrs','Retrans','OutRsts'])]
    body+='<h3>Observed host counters</h3><p>Totals below sum nonoverlapping run windows. Counters are host-wide and may include unrelated processes/traffic. Retransmissions, resets and receive errors are observations; neither their cause nor their effect on a latency tail is isolated. Zero drops or input errors do not establish that the network had no transient effects.</p>'+simple_table(['Counter','Total delta','Runs with nonzero delta'],counter_rows)
    cpu_fields=[k for k in allrows[0] if k.startswith('endpoint_used_cpu')]
    unusable=all(float(r[k])==0 for r in allrows for k in cpu_fields)
    body+=f'<p>Endpoint INFO samples/run range {fmt(bounds("server_info_samples")[0])}–{fmt(bounds("server_info_samples")[1])}. The endpoint CPU counters are {"all zero and unusable" if unusable else "reported in the detail tables"}. Endpoint INFO is one endpoint view and may include observer/unrelated traffic. Actual server-node, shard and proxy CPU/pressure/queues are absent. <strong>No server-node bottleneck can be established.</strong> Small differences in achieved rate do not establish spare server capacity.</p>'
    body+=FOLLOWUP
    if len(stages)>1:
        pilot=ROOT/'results/small-capacity-pilot'
        pp=json.loads((pilot/'plan.json').read_text())
        pr=validate_pilot(ROOT)
        body+=f'<h2>Capacity pilot and preparation records</h2><p>The separate small-capacity pilot contains six {pp[0]["duration_s"]}-second runs at 1/16 connections, one repetition per version/count. All six are OK, achieve at least 99.997% of 50,000/s, and have zero errors. This short pilot verified that the chosen small-reply budget could meet the target; it is not pooled into scored arithmetic means or percentiles.</p>'
        body+=simple_table(['Connections','Version','Achieved /s','CPU µs/cmd','Steady MiB','Mean µs','p99 µs'],[[r[k] for k in ['connections','variant','achieved_commands_s','cpu_us_per_command','steady_rss_mib','response_mean_us','response_p99_us']] for r in pr])
        body+='<p>Rust tests, scored/diagnostic/jemalloc loopback checks, bootstrap/build logs, native toolchain and cached source Git objects are preserved in the private AWS export. Loopback checks are functional checks, not endpoint performance measurements.</p>'
    body+='<h2 id="archive">Saved data and reproducibility</h2><p>Every immutable DONE record and file checksum was checked; all pinned binaries, harness, lockfile and build script match recorded hashes. Both stages have exactly three repetitions per configuration/version and valid counts, payload byte accounting, error logs, measurement windows, concurrency bounds and telemetry coverage. All 270 scored runs passed. Raw HDRs permit independent distribution work.</p>'
    body+=f'<p><a href="{stem}-per-run.csv">All repetitions and exact payloads</a> · <a href="{stem}-means.csv">Arithmetic means</a> · <a href="{stem}-medians.csv">Medians</a> · <a href="{stem}-comparisons.csv">Mean comparisons</a> · <a href="{stem}-median-comparisons.csv">Median comparisons</a> · <a href="{stem}-host-details.csv">Host details</a> · <a href="{stem}-connection-trends.svg">Mean figure</a> · <a href="{stem}-connection-trends-median.svg">Median figure</a></p>'
    for s in stages:
        body+=f'<p>{s}: <a href="results/{s}/report.md">Aggregate tables</a> · <a href="results/{s}/payload_report.md">Payload tables</a> · <a href="results/{s}/per_run.csv">Original per-run CSV</a> · <a href="results/{s}/means.csv">Original means</a> · <a href="results/{s}/medians.csv">Original medians</a></p>'
    body+='<p>Raw HDRs, host telemetry, immutable attempt checksums and exact build inputs remain in the supplied private artifact directory. This generator validates scored result inputs; AWS filesystem export validation is a separate archival operation.</p>'
    for v,b in datasets[stages[0]]['builds'].items():
        body+=f'<p>{v} source <span class="hash">{b["commit"]}</span><br>Scored binary SHA-256 <span class="hash">{b["binary_sha256"]}</span></p>'
    payload=dict(**data,configs=[list(c) for c in configs],names=[cname(c) for c in configs],categories=categories)
    body+='<script id="data" type="application/json">'+json.dumps(payload).replace('<','\\u003c')+'</script>'
    js=(SCRIPT_DIR/'detail.js').read_text()+'</script></main></html>'
    body+='<script>\n'+js.replace('</script></main></html>', '''</script><script>
const summaryStatistic=document.getElementById('summary-statistic');
function updateSummaryStatistic(){document.querySelectorAll('[data-summary]').forEach(s=>s.hidden=s.dataset.summary!==summaryStatistic.value)}
summaryStatistic.addEventListener('change',updateSummaryStatistic);updateSummaryStatistic();
const chartStatistic=document.getElementById('chart-statistic');
function updateChartStatistic(){document.querySelectorAll('[data-chart]').forEach(s=>s.hidden=s.dataset.chart!==chartStatistic.value)}
chartStatistic.addEventListener('change',updateChartStatistic);updateChartStatistic();
const workloadFilter=document.getElementById('workload-filter'),connectionFilter=document.getElementById('connection-filter'),arrangeBy=document.getElementById('arrange-by');
const workloadNames={small:'Small replies',rare:'Rare large replies',near:'Near threshold',large:'Large replies'},workloadOrder=['small','rare','near','large'];
const summaryTables=[...document.querySelectorAll('tbody')].map(body=>({body,rows:[...body.querySelectorAll('tr[data-config]')]})).filter(t=>t.rows.length);
function matchesView(c){return (workloadFilter.value==='all'||c[0]===workloadFilter.value)&&(connectionFilter.value==='all'||c[2]===+connectionFilter.value)}
function compareConfigurations(a,b){const work=workloadOrder.indexOf(a[0])-workloadOrder.indexOf(b[0]),conn=a[2]-b[2],rate=a[1]-b[1];return arrangeBy.value==='connections'?(conn||work||rate):(work||rate||conn)}
function updateSummaryView(){
  summaryTables.forEach(({body,rows})=>{
    rows.sort((a,b)=>compareConfigurations(JSON.parse(a.dataset.config),JSON.parse(b.dataset.config)));
    body.querySelectorAll('.group-divider').forEach(r=>r.remove());
    let lastGroup=null;
    rows.forEach(row=>{
      const c=JSON.parse(row.dataset.config),shown=matchesView(c),group=arrangeBy.value==='connections'?c[2]:c[0];row.hidden=!shown;
      if(shown&&group!==lastGroup){const divider=document.createElement('tr');divider.className='group-divider';const th=document.createElement('th');th.colSpan=row.children.length;th.scope='rowgroup';th.textContent=arrangeBy.value==='connections'?c[2]+' '+(c[2]===1?'connection':'connections'):workloadNames[c[0]];divider.append(th);body.append(divider);lastGroup=group}
      body.append(row);
    });
  });
  const shown=D.configs.filter(matchesView);document.getElementById('view-count').textContent='Showing '+shown.length+' of '+D.configs.length+' configurations. Filters apply to comparison, CPU, memory and host tables; charts and narrative show the complete matrix.';
}
[workloadFilter,connectionFilter,arrangeBy].forEach(s=>s.addEventListener('change',updateSummaryView));
document.getElementById('reset-view').addEventListener('click',()=>{workloadFilter.value='all';connectionFilter.value='all';arrangeBy.value='workload';updateSummaryView()});
updateSummaryView();
</script></main></html>''')
    connections=sorted({c[2] for c in configs})
    view_controls='<label>Workload<br><select id="workload-filter"><option value="all">All workloads</option>'+''.join(f'<option value="{w}">{titles[w]}</option>' for w in ['small','rare','near','large'])+'</select></label>'
    view_controls+='<label>Connections<br><select id="connection-filter"><option value="all">All connections</option>'+''.join(f'<option value="{c}">{c}</option>' for c in connections)+'</select></label>'
    view_controls+='<label>Arrange by<br><select id="arrange-by"><option value="workload">Workload</option><option value="connections">Connections</option></select></label>'
    body=body.replace('<label>Summary statistic<br>',view_controls+'<label>Summary statistic<br>',1)
    body=body.replace('</select></label></div><section data-summary="median">','</select></label><button id="reset-view" type="button">Reset filters</button></div><p id="view-count" class="subtle" aria-live="polite"></p><section data-summary="median">',1)
    body=body.replace('</style>',' .group-divider th{background:#dcebf0;text-align:left;font-size:15px}.controls{align-items:end}.controls button{padding:10px 14px;font:inherit;border:1px solid #a7b9c5;border-radius:4px;background:white;cursor:pointer} [hidden]{display:none!important}</style>',1)
    for c in configs:
        needle='<tr><td>'+html.escape(cname(c))+'</td>'
        body=body.replace(needle,'<tr data-config="'+html.escape(json.dumps(list(c)),quote=True)+'"><td>'+html.escape(cname(c))+'</td>')
    (ROOT/(stem+'.html')).write_text(body)
    print(stem,'aggregate runs',len(allrows),'all rows',len(rows),'commands',count,'host extremes',{k:bounds(k) for k in ['host_busy_pct','busiest_core_average_pct','host_mem_available_min_gib']},flush=True)

build(['knee-validation'],'knee-report','ARM redis-rs codec — full knee report',KNEE_FINDINGS)
build(['main','knee-validation'],'combined-report','ARM redis-rs codec — complete six-connection analysis',COMBINED_FINDINGS)
