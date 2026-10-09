# redis-rs codec benchmarks

A standalone ECHO harness for comparing CPU, latency and memory behavior with
async codec-buffer trimming enabled and disabled in redis-rs.

**This is the follow-up harness, not the original harness that produced the earlier
ten-minute results discussed in [redis-rs issue #2430](https://github.com/redis-rs/redis-rs/issues/2430).**
The follow-up matrix has not run yet. Short correctness checks have passed; those
checks are not performance results. Sources under comparison are pinned to
[PR #2428](https://github.com/redis-rs/redis-rs/pull/2428) and its upstream base.

The repository contains source, scripts, dependency pins and reproducible workload
plans. Target addresses, credentials, raw results and machine-specific output stay
outside version control.

## Questions

1. Does the disabled patch preserve upstream CPU, latency and memory behavior?
2. Why did one multiplexed connection have higher steady and peak RSS with trimming?
3. How do physical connection count and multiplexing depth affect the tradeoff?
4. Do explicit pipelines change that tradeoff?

## Three primary versions

* Baseline: clean upstream `018df9b148fa4c1c4ecc1050f9fb10df8a32cddf`.
* Patched-off: PR commit `7bb03646438ae6abd116be652932bf4f7b822a37`, threshold `None`.
* Patched-on: same PR commit, threshold 64 KiB; replacement capacity 8 KiB.

The baseline was upstream main when the PR opened, not the exact 1.7.1 release tag.
Both sources are built natively using Rust 1.98.1 and one shared Cargo.lock.
The first dependency resolution is frozen in that lockfile; it is not claimed to
reproduce the unavailable old benchmark lockfile. Baseline and patched dependency
hashes must match before the runner starts.

Default scored builds use the system allocator. Diagnostic builds track requested
live bytes, allocation/reallocation/free counts and requested-byte churn and add codec counters. Those
builds are separate from scored builds. Optional jemalloc builds are a separate
allocator experiment; compare baseline and patch within each allocator.

## Host requirements

Scored runs require Linux; native x86_64 and AArch64 builds use the same sources.
Record kernel, glibc, CPU model, memory/cgroup limits, allocator settings and network
counters. Optional IMDSv2 collection records instance type, not instance IDs or
deployment location. Missing server telemetry is explicit. Compare versions on the
same host and software environment; separate results across different architectures.

Requirements: Rust 1.98.1, Python 3, Git, a C/C++ build toolchain, and a reachable
Redis-compatible endpoint. An optional Linux bootstrap helper supports DNF and APT
to install the toolchain and monitoring utilities. For other package managers,
install equivalent packages. No setup/build should overlap measured traffic.
Linux loopback CI validates telemetry and both allocators. The full performance
matrix has not run yet.

Local checks cover builds, unit tests, all four workloads, explicit pipelines,
single-spike probes, resume and connection failure. These checks validate correctness,
not performance. The full matrix must run on the target Linux
host. GitHub CI checks both allocators against loopback Redis with Linux telemetry;
it does not run the performance matrix. A cause for the previous single-connection RSS increase is not yet established.

## Workloads

| Pattern | Payload bytes | Aggregate target commands/s |
| --- | --- | --- |
| Small | Equal-probability 128 B, 512 B, 1 KiB, 4 KiB | 50,000 |
| Rare | 4 KiB; independently selected approximately 0.1% 5 MiB | 2,000 |
| Near | Equal-probability 8,24,40,56,72,88,104,120 KiB | 2,000 |
| Large | Equal-probability 4,5,6 MiB | 50 and 200 |

ECHO exercises requests and responses. It still consumes server CPU and bandwidth.
The rare distribution averages approximately 9.1 KiB; do not label its overall
average as 4 KiB. Payload templates are shared across connections, not copied per
connection, and their resident footprint is included in all variants. Each response
is checked for size and first/last 16-byte markers, without scanning megabytes in
the scored hot path. Decoded responses are dropped before recording completion.

All variants in a repetition use the same seed and offered command sequence.
Capacity skips and submission-deadline skips are counted separately; they are not silently queued without a
bound. The scheduler uses documented 1 ms ticks. At high rates it consequently
submits small groups per tick, which can affect batching and latency. This pacing
is identical across versions but should not be presented as exact reproduction of
the unavailable earlier harness. Payload selection depends on global sequence,
not each connection's command counter. Random rare spikes reduce synchronized
connection bursts. Record per-connection large-command coverage.

## Connections and concurrency

Sweep 1,2,4,8,16 physical connections with fixed aggregate concurrency **per
workload**: small 128, rare/near 64, large 16. This preserves the old 1- and
16-connection concurrency settings while adding intermediate counts. The 100-
connection reference uses one request per connection and is labelled as a different
aggregate concurrency budget. The diagnostic sweep uses one connection with
1,4,16,64 outstanding commands to separate socket count from multiplexing depth.
Clones share each MultiplexedConnection's physical socket. Four Tokio workers
drive asynchronous requests; concurrency does not imply one OS thread per request.

Explicit pipelines are a separate screening phase: matched controls with batch 1,
plus batches of 4 or 16, with total
outstanding command budget 64 across 1,4,16 connections where that is possible.
The external semaphore counts pipeline members, not batches; the client default
internal concurrency policy is unchanged. Rate is commands/s, not batches/s.
Pipeline members are attributed batch completion latency; individual reply arrival
times are not measured and must not be claimed. Record total batch/request bytes.

## Configure and inspect the matrix

[`matrix.json`](matrix.json) is the editable source of truth. It defines workload
rates and concurrency budgets, variants, worker count, seeds, connection counts,
repetitions, durations, warm-up/idle periods and pipeline sizes for each stage.
Payload distributions and the enabled 64 KiB threshold are defined in the Rust
harness; changing those requires a rebuild. This matrix compares the existing patch,
not new trimming policies.

Generate frozen JSON plans and a readable stage inventory:

```sh
python3 scripts/make_plans.py
python3 scripts/make_plans.py --list
```

See [`plans/README.md`](plans/README.md) for run counts, build modes, concurrency,
batch sizes and estimated runtime. Each JSON plan contains the full cases and its
build mode. The runner automatically chooses scored, diagnostic or jemalloc binaries
from that mode; a contradictory command-line build flag is rejected.

For example, select a smaller knee study without editing the default plans:

```sh
python3 scripts/make_plans.py --stage knee-validation --connections 2,4,8 \
  --profiles rare,large-200 --repetitions 3 --duration-s 300 --workers 4 \
  --output plans/custom
```

`--variants baseline,patched-off` selects only the disabled-path comparison.
Workload rates, pipeline sizes, single-connection in-flight sweeps, observation
periods and optional stage selections can be edited in `matrix.json`. For custom counts that do not divide a concurrency budget, the
per-connection limit is rounded down (minimum one); the inventory shows the actual
aggregate limit.
Review pilots before interpreting LIMITED results. Generating plans never starts traffic.

| Stage | Purpose |
| --- | --- |
| small-capacity-pilot | Check small-workload capacity at 1 and 16 connections |
| screening | All four patterns at 1,2,4,8,16 connections, two measured minutes |
| main | All patterns at 1,16,100, five minutes, three repetitions |
| knee-validation | Repeated five-minute comparisons at 2,4,8; filter after screening |
| diagnostic | One connection, 1/4/16/64 in flight; rare, large at both rates, single spike and burst |
| pipeline-screening | Rare and large/200 with matched batch-1/4/16 controls and budget 64 |
| synchronized-stress | Separately labelled synchronized rare spikes |
| jemalloc-screening | Optional matched versions using jemalloc |
| confirmation | Candidate 15-minute runs for known anomalies; select after screening |

Optional stages are available, not an unconditional launch of every combination.
Every comparison and diagnostic stage defaults to **three repetitions**; only the
short capacity pilot runs once. `--repetitions N` overrides the selected stages.
Generating every plan does not run them. See the inventory before choosing stages:
the primary `main` stage takes at least **13.57 hours**, excluding setup/drain;
optional stages add their own time.

These estimates exclude connection setup, drain and provisioning. Stage selection
is intentional: extend unresolved comparisons, not every case. Intermediate
connection counts around a discovered knee need repeated five-minute validation;
the initial screen is exploratory. Main runs use 30-second warm-up, five measured
minutes and 30-second observation with connections retained. Commands started
inside the measured window may complete afterward; drain time is reported and CPU
includes that drain. Connection-drop observations are labelled separately.

The one-spike diagnostic sends small traffic for five seconds, one 5 MiB command,
then no additional commands while retaining the connection. Repeated-burst probes
offer one second of large traffic every 20 seconds. These deliberate probes are not
scored as steady workloads. Include synchronized rare spikes only as a separately
labelled stress case.

## Metrics and display

See the [metric reference](docs/METRICS.md) for definitions, units, observation
windows, diagnostic counters and limitations, and the
[illustrative report](docs/REPORT_EXAMPLE.md) for the table layout.

Analysis creates `report.md` and `payload_report.md`, with every repetition followed
by its **arithmetic mean**. It also creates `per_run.csv`, `means.csv` and
`medians.csv`; raw runs and median summaries remain available. Mean/median run
percentiles are not pooled percentiles. Missing runs and missing metrics are visible.

### Retained measurements

* Per-run configuration, binary SHA-256, source commit, dependency lockfile hash,
  environment snapshot, completed/offered/not-submitted counts, errors, achieved rate.
* User/system CPU, total CPU per command, actual peak outstanding commands and
  payload bytes. CPU belongs to the whole client process, including harness work.
* RSS at 250 ms, process high-water RSS, steady RSS over the final two minutes,
  average RSS, RSS slopes, final idle RSS and connection-drop RSS (before result serialization). Slopes help
  select longer confirmations; they do not prove a leak or steady state. The process high
  water includes warm-up; sampled measured peaks are reported separately. Shorter
  than 250 ms spikes can be missed by the time series, but the process high water
  still detects their maximum without precise timing.
* Exact payload-size and merged overall/ordinary/spike HDR histograms (3 significant digits) and mean/p50/p99/p99.9/max
  for submission-to-completion, scheduled-to-completion and scheduling delay.
  Record sample count; suppress p99.9 when it equals max or has fewer than 10,000
  observations. The CSV leaves unreportable p99.9 blank. Skipped offered requests are visible as not-submitted, not invented
  latency samples. Median run percentiles and pooled percentiles are distinct.
* Host CPU, memory, pressure, network bytes/errors/retransmissions each second;
  process status and smaps_rollup. The analyzer reports measured-window host deltas
  and sample spans. Optional `--server-info` samples endpoint INFO counters every
  five seconds using `redis-cli`; this covers one endpoint, not all cluster nodes.
  Unavailable counters are explicit. INFO adds a small observer load.
* Diagnostic requested-live/peak bytes, allocation counts and requested-byte churn; read/write observed
  capacity maxima, trim counts and read trims with incomplete versus complete
  decoded responses. Counter deltas isolate the measured window; growth counts,
  observed capacity changes and replacement requested capacity are also retained.
  Reallocation churn counts the old requested size as freed and the new requested
  size as allocated, even when libc resizes in place. BytesMut capacity can hide retained storage after advance.
  Observed growth/capacity totals do not equal actual allocated or reclaimed bytes.
  Diagnostic peaks include warm-up. Allocation counters do not measure malloc rounding, fragmentation, libc internal
  realloc overlap, kernel socket buffers, or allocation stacks. Separate profiling
  is needed if these counters leave the cause unresolved.

## Build and run

Run these steps **on the Linux benchmark client**, from the repository directory.
Commands that generate plans or build binaries do not start traffic.

### 1. Get the code and tools

```sh
git clone https://github.com/ofirluzon/redis-rs-codec-bench.git
cd redis-rs-codec-bench
```

Install Rust 1.98.1, Python 3 and a C/C++ build toolchain. On Amazon Linux 2023
or Ubuntu/Debian, the optional helper installs these and monitoring utilities:

```sh
bash scripts/bootstrap-linux.sh
source "$HOME/.cargo/env"
```

The helper requires Git to have been installed before cloning. For other Linux
distributions, install the [host prerequisites](#host-requirements) yourself.
Finish setup and builds before any measured traffic.

### 2. Build the three comparison versions

```sh
python3 scripts/build.py
```

This builds two binaries: baseline and patched. The runner uses the patched binary
with trimming off and on, producing the three comparison versions automatically.
No diagnostic or jemalloc build is needed for the normal comparison.

### 3. Choose and inspect a plan

```sh
python3 scripts/make_plans.py
python3 scripts/make_plans.py --list
```

Start with `plans/small-capacity-pilot.json`: six 30-second cases, one repetition.
For the primary comparison, use `plans/main.json`: 135 five-minute cases, three
repetitions, at least 13.57 hours. Each case is one version of one configuration.
See [the inventory](plans/README.md) for the other stages and
[matrix configuration](#configure-and-inspect-the-matrix) for shorter/custom plans.

### 4. Set the endpoint and run

Set `REDIS_URL` to your test endpoint in the current shell. This loopback URL is
only an example; use your own endpoint for the benchmark:

```sh
export REDIS_URL=redis://127.0.0.1:6379
python3 scripts/run_matrix.py plans/small-capacity-pilot.json --results results/pilot
```

After checking pilot capacity, start the primary comparison explicitly:

```sh
python3 scripts/run_matrix.py plans/main.json --results results/main
```

To collect endpoint INFO too, append `--server-info` to the run command and install
`redis-cli`. This observes one endpoint, not every cluster node. Choose this option
before starting: a resumed run must use the same collector setting.

If a run is interrupted, repeat **the same command** to resume. Completed attempts
are verified and skipped; incomplete attempts are retained and retried separately.
Only one matrix runs per host/user at a time. The runner rejects a changed plan,
build, host, endpoint identity or environment in an existing result directory.

### 5. Read and retain the results

```sh
python3 scripts/analyze.py results/pilot
python3 scripts/analyze.py results/main
```

Analyze the directory you ran; you do not need to wait for the whole matrix.
Open `report.md` for overall/ordinary/spike tables or `payload_report.md` for exact
sizes. Each table shows individual runs plus their mean, CPU, RSS and latency;
status and achieved rate follow. CSVs retain all metrics and the alternative
median summaries. See [metrics and reports](docs/METRICS.md).

Zero command errors are required. An achieved rate below 95% of target is labelled
`LIMITED`; it is a completed measurement, not a claim that the target was reached.
Only checksum-validated DONE attempts enter analysis.

Copy results off the host during long stages and before deleting the instance.
From your local computer, supply your SSH alias and the remote results path:

```sh
python3 scripts/backup_results.py YOUR_SSH_ALIAS /path/to/results ./saved-results --every-seconds 600
```

Keep the whole result directory, including raw histograms, telemetry and the `kit/`
snapshot of source, documentation, lockfile and exact binaries. Backups include
active attempts too; analysis still excludes incomplete attempts. Raw results can
contain machine details, paths or endpoint information in errors. Keep them private
and share reviewed summaries. The endpoint URL is not stored as run configuration.

### Optional experiments

Build only the additional mode you intend to run:

| Experiment | Build first | Run plan |
| --- | --- | --- |
| Allocation/codec diagnostics | `python3 scripts/build.py --diagnostics` | `plans/diagnostic.json` |
| jemalloc comparison | `python3 scripts/build.py --jemalloc` | `plans/jemalloc-screening.json` |

Use the same `run_matrix.py PLAN --results DIRECTORY` command with a separate result
directory for each stage. The plan selects the correct binaries automatically.
Diagnostic builds add instrumentation overhead and are separate from scored results.

### Optional correctness checks

These use an isolated loopback Redis, not your configured benchmark endpoint.
Install `redis-server`, build scored and diagnostic binaries, then run:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/local_check.py
```

After a jemalloc build, `python3 scripts/local_check.py --jemalloc` checks that mode.
After `build.py` prepares upstream sources, Rust checks are:

```sh
cargo test --locked --features patched
cargo clippy --locked --features patched,alloc-diagnostics,codec-diagnostics -- -D warnings
```

TTL, pooling and the single-connection exception are design candidates only, not
implemented binaries. First explain the existing regression. Implement any chosen
policy in an isolated experimental copy, with explicit byte/time bounds and its
own tests, and compare using the same frozen sources and environment.
