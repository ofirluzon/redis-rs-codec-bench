"""Create a portable review copy; preserve the private reports and raw records."""
import hashlib, html, json, pathlib, re

from report_data import interpretation, artifact_root, validate_report_data
ROOT=artifact_root()
source=(ROOT/'combined-report.html').read_text()
match=re.search(r'<script id="data" type="application/json">(.*?)</script>',source,re.S)
data=json.loads(match[1])
validate_report_data(data, ROOT)
for kind in ['rows','means','medians']:
    for row in data[kind]:
        if 'id' in row:
            row['id']=re.sub(r'^(?:main|knee-validation)-','codec-',row['id'])
        row['stage']='Group A: 1/16/100 connections' if row['stage']=='main' else 'Group B: 2/4/8 connections'

summary=interpretation(ROOT, 'build_share_report-16')
(ROOT/'redis-rs-codec-summary.txt').write_text(summary)

executive=interpretation(ROOT, 'build_share_report-28')

out=source[:match.start()]+'<script id="data" type="application/json">'+json.dumps(data,separators=(',',':')).replace('<','\\u003c')+'</script>'+source[match.end():]
old_findings=re.search(r'<h2 id="findings">.*?(?=<h2 id="charts">)',out,re.S)[0]
detail=old_findings.replace('id="findings"','id="full-findings"',1)
out=out.replace(old_findings,executive+'<details><summary>Detailed findings and interpretation</summary>'+detail+'</details>',1)
out=out.replace('<title>ARM redis-rs codec — complete six-connection analysis</title>','<title>redis-rs codec buffer trimming — ARM benchmark</title>')
out=out.replace('<h1>ARM redis-rs codec — complete six-connection analysis</h1>','<h1>redis-rs codec buffer trimming — ARM benchmark</h1>')
out=out.replace('Scored data completed October 10, 2026 at 6:39 PM PDT. Analysis and private export completed October 12, 2026.','Measured October 9–10, 2026 · 1 / 2 / 4 / 8 / 16 / 100 physical connections · baseline / patched-off / patched-on')
out=out.replace('Main and knee are sequential stages, not simultaneous trials.','The 1/16/100 cases were collected first, followed by 2/4/8 in a separate sequential group; these were not simultaneous trials.')
out=re.sub(r'<h3>What the “knee” establishes</h3><p>.*?</p>','<h3>What the connection-count comparison establishes</h3><p>At these offered rates and aggregate budgets, memory retention changes with physical connection count. All targets were met. This does not establish maximum throughput or a saturation point. The 100-count aggregate concurrency budget differs, and the two measurement groups occurred at different times.</p>',out,flags=re.S)
out=out.replace('In knee large/200/s, on','For large/200/s at 2/4/8 connections, on')
out=out.replace('Each scored stage has 5 profiles × 3 physical connection counts × 3 versions × 3 repetitions = 135 runs.','The complete matrix has 5 workload/rate profiles × 6 physical connection counts × 3 versions × 3 repetitions = 270 runs.')
out=out.replace('Hardware/build records are preserved privately: native ARM','Hardware/software: native ARM').replace(' No raw connection credentials or host addresses are embedded in this report.','')
out=out.replace('No optional stage was queued. The completed benchmark is ready for independent analysis from local artifacts; shutting down the client instances does not affect these saved reports.','These follow-up experiments have not been run.')
out=out.replace('Rust tests, scored/diagnostic/jemalloc loopback checks, bootstrap/build logs, native toolchain and cached source Git objects are preserved in the private AWS export. Loopback checks are functional checks, not endpoint performance measurements.','Preparation included Rust tests and scored/diagnostic/jemalloc loopback checks. These functional checks passed; they are not endpoint performance measurements.')
out=out.replace('Small replies · 50,000/s · 1 connections','Small replies · 50,000/s · 1 connection').replace('Rare large replies · 2,000/s · 1 connections','Rare large replies · 2,000/s · 1 connection').replace('Near threshold · 2,000/s · 1 connections','Near threshold · 2,000/s · 1 connection').replace('Large replies · 50/s · 1 connections','Large replies · 50/s · 1 connection').replace('Large replies · 200/s · 1 connections','Large replies · 200/s · 1 connection')
data['names']=[n.replace('· 1 connections','· 1 connection') for n in data['names']]
# Reinsert the edited display names without changing any numeric observations.
out=re.sub(r'<script id="data" type="application/json">.*?</script>',lambda _: '<script id="data" type="application/json">'+json.dumps(data,separators=(',',':')).replace('<','\\u003c')+'</script>',out,flags=re.S)

prov='''<h2 id="archive">Data and provenance</h2><p>The embedded dataset contains every scored repetition and aggregate/exact-payload metrics, plus arithmetic means and medians. File checksums, pinned binary/source identity, counts, measurement windows, error logs, payload accounting and telemetry coverage were validated. Raw HDRs, binaries and supporting records are retained separately; this attachment contains report metrics and charts.</p><details><summary>Optional downloads from this report</summary><p>These downloads are generated locally from the embedded data; no companion files or network access are needed.</p><div class="controls"><button type="button" data-download="rows">All per-run metrics (CSV)</button><button type="button" data-download="means">Arithmetic means (CSV)</button><button type="button" data-download="medians">Medians (CSV)</button><button type="button" data-figure="mean">Mean charts (SVG)</button><button type="button" data-figure="median">Median charts (SVG)</button></div></details>'''
original=json.loads(match[1])
private_root=ROOT/'results/main/builds.json'
for v,b in json.loads(private_root.read_text()).items():
    prov+=f'<p>{html.escape(v)} source <span class="hash">{b["commit"]}</span><br>Scored binary SHA-256 <span class="hash">{b["binary_sha256"]}</span></p>'
out=re.sub(r'<h2 id="archive">.*?(?=<script id="data")',lambda _:prov,out,flags=re.S)
out=out.replace('>Saved data</a>','>Data and provenance</a>')
download_js=r'''<script>
function saveEmbeddedFile(name,content,type){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function exportEmbeddedCsv(kind){const rows=D[kind],keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];const escape=v=>{const s=v===undefined||v===null?'':String(v);return /[",\r\n]/.test(s)?'"'+s.replaceAll('"','""')+'"':s};const csv=[keys.map(escape).join(','),...rows.map(r=>keys.map(k=>escape(r[k])).join(','))].join('\r\n');saveEmbeddedFile('redis-rs-codec-'+kind+'.csv','\uFEFF'+csv,'text/csv;charset=utf-8')}
document.querySelectorAll('[data-download]').forEach(b=>b.addEventListener('click',()=>exportEmbeddedCsv(b.dataset.download)));
document.querySelectorAll('[data-figure]').forEach(b=>b.addEventListener('click',()=>saveEmbeddedFile('redis-rs-codec-'+b.dataset.figure+'-charts.svg',document.querySelector('[data-chart="'+b.dataset.figure+'"] svg').outerHTML,'image/svg+xml')));
</script>'''
out=out.replace('</main></html>',download_js+'</main></html>')

for kind in ['rows','means','medians']:
    assert len(data[kind])==len(original[kind])
    for a,b in zip(data[kind],original[kind]):
        assert {k:v for k,v in a.items() if k not in ['id','stage']}=={k:v for k,v in b.items() if k not in ['id','stage']}
assert len({r['id'] for r in data['rows']})==270
for bad in ['knee','aws-export','/Users/','/home/ec2-user','172.31.','redislabs.com','.pem','private export']:
    assert bad not in out,bad
for link in re.findall(r'\b(?:href|src)="([^"]*)"',out):
    assert link.startswith('#'),link
ids=set(re.findall(r'\bid="([^"]+)"',out))
for link in re.findall(r'<a[^>]+href="#([^"]+)"',out):assert link in ids,link

path=ROOT/'redis-rs-codec-arm-report.html';path.write_text(out)
validation={'scored_runs':270,'embedded_per_run_rows':len(data['rows']),'embedded_mean_rows':len(data['means']),'embedded_median_rows':len(data['medians']),'numeric_metrics_unchanged':True,'external_resources':0,'local_file_links':0,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
(ROOT/'share-report-validation.json').write_text(json.dumps(validation,indent=2))
print(json.dumps(validation,indent=2))
