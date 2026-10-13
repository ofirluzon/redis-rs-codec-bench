# Reproducing the October 2026 ARM HTML reports

The scripts in [`reports/arm_20261009`](../reports/arm_20261009) preserve the
analysis and interactive reports built for the completed ARM experiment. They
load **experiment-specific interpretation** from a separate private JSON file,
not a generic statistical model.
They reject metric CSVs that differ from the frozen experiment. Use
`scripts/analyze.py` for other runs, partial matrices or LIMITED measurements.
The frozen `knee-validation` plan name is retained for reproducibility; the sharing
report calls it a connection-count comparison, not a saturation knee.

This repository publishes generation code only. Raw results, exact binaries,
HDRs, telemetry, rendered reports and AWS exports stay outside version control.
The archive's `results/main`, `results/knee-validation` and
`results/small-capacity-pilot` directories must retain their original analyzed
CSVs, plans, DONE records, attempts and `kit/` snapshots. It must also contain
`interpretation/arm_20261009.json`, the saved report commentary/templates from
that experiment. This private input travels with the local archive and is not
published in GitHub. A fresh clone alone cannot recreate the measured report.

## Setup and generation

From this repository, create an optional plotting environment:

```sh
python3 -m venv reports-output/venv
reports-output/venv/bin/python -m pip install -r reports/arm_20261009/requirements.txt
```

Set `CODEC_REPORT_ARTIFACTS` to the local private artifact directory containing
`results/`. Derived HTML, CSV and SVG files are written into that directory. The
commands read the archived results without changing the raw data or existing
Markdown/CSV analysis inputs. Generated outputs with the same names are replaced;
use a separate directory with a `results` symlink to preserve old rendered copies.
No command below connects to Redis or AWS, or starts benchmark traffic.

```sh
reports-output/venv/bin/python reports/arm_20261009/build_main_report.py "$CODEC_REPORT_ARTIFACTS"
reports-output/venv/bin/python reports/arm_20261009/build_full_reports.py "$CODEC_REPORT_ARTIFACTS"
reports-output/venv/bin/python reports/arm_20261009/build_share_report.py "$CODEC_REPORT_ARTIFACTS"
```

The main generator preserves the historical 1/16/100 report, including the
contemporaneous note that intermediate runs were still in progress. The full
and sharing reports describe the completed six-count experiment.

| Output | Purpose |
| --- | --- |
| `main-report.html` | Historical 135-run main report |
| `knee-report.html` | Intermediate 2/4/8 subset; no additional scored measurements |
| `combined-report.html` | All 270 scored runs with companion CSV/SVG links |
| `redis-rs-codec-arm-report.html` | Standalone sharing copy; all charts/data embedded, no companion-file links |
| `redis-rs-codec-summary.txt` | Short memory/CPU/outlier summary |
| `share-report-validation.json` | Export counts, metric-preservation checks and HTML checksum |
| `*-per-run.csv`, `*-means.csv`, `*-medians.csv` | Individual and aggregated metrics |
| `*-comparisons.csv`, `*-median-comparisons.csv` | Percentage comparisons |
| `*-host-details.csv`, `*-connection-trends*.svg` | Host observations and mean/median figures |

Only the standalone sharing HTML is needed by a reader. It includes locally
created CSV/SVG downloads, mean/median selectors, workload/connection filters,
arrangement by workload or connections, and every repetition. Its figures show
RSS, CPU per command and response latency. Median latency chart lines summarize
three **run mean response times**, not p50 or pooled percentiles. Means or medians
of run percentiles are also not pooled percentiles. Raw HDRs remain separate.

## Validation and limits

The loaders check canonical CSV fingerprints, three matched repetitions,
key arithmetic means, retained kit hashes and each scored immutable attempt via
the harness validator (checksums, errors, counts, payloads, windows and telemetry).
The sharing exporter checks that embedded metrics match the frozen input CSVs,
then verifies that renaming collection groups did not change numeric observations
and that links need no companion files. It removes private archive references and
uses neutral collection-group labels. Review generated content before sharing;
this is not a sanitizer for arbitrary input HTML.

Source commit and binary hashes remain in the reports. The sharing copy does not
contain raw endpoint addresses, credentials or AWS export records. These generators
do not recreate the AWS archival operation, validate its filesystem manifests,
run optional experiments, or pool HDR distributions.

Two collection groups ran sequentially, and 100 connections used a different
aggregate in-flight budget. Actual server-node telemetry was unavailable. Those
limitations remain visible in the sharing report. All targets being met does not
identify maximum throughput or a saturation point.

Plot styling may vary across Python/Matplotlib platforms; numeric observations
and aggregation are the reproducibility contract, not byte-identical SVG output.
