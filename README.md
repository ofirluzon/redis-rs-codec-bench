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
live bytes and allocation/reallocation/free counts and add codec counters. Those
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
install equivalent packages. No setup/build should overlap measured traffic. Full Linux
performance runs and Linux telemetry have not yet been validated by this kit.

Local preparation checks passed: both scored and diagnostic source versions build;
three unit tests; twelve short baseline/off/on workload and pipeline cases; six
single-spike probes across scored/diagnostic builds. Resume skips validated results,
and connection failure remains an incomplete attempt. These loopback checks
validate the harness, not performance or Linux RSS collection. The patched-on
single-spike diagnostic observed one completed-response read trim and one write
trim; disabled and baseline observed none. No partial-response trim was observed
in that short probe, so that proposed explanation remains unproven.

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
Incomplete offered commands are counted; they are not silently queued without a
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

Explicit pipelines are a separate screening phase: batches of 4 or 16, with total
outstanding command budget 64 across 1,4,16 connections where that is possible.
The external semaphore counts pipeline members, not batches; the client default
internal concurrency policy is unchanged. Rate is commands/s, not batches/s.
Pipeline members are attributed batch completion latency; individual reply arrival
times are not measured and must not be claimed. Record total batch/request bytes.

## Stages, not one unconditional giant launch

Generate plans with `python3 scripts/make_plans.py`. Review short capacity pilots
before interpreting LIMITED results. Do not automatically start all plans.

* `small-capacity-pilot`: six 30-second baseline/off/on cases for 1 and 16 connections.
* `screening`: 75 two-minute cases across 1,2,4,8,16, approximately three hours
  including warm-up and post-traffic observation.
* `main`: 135 five-minute cases across 1,16,100, three repetitions, approximately
  13.6 hours including warm-up and post-traffic observation.
* `diagnostic`: 48 three-minute allocation/codec-instrumented cases, approximately
  3.4 hours. Includes rare, large, a single spike followed by no commands, and bursts.
* `pipeline-screening`: 30 three-minute cases, approximately 1.9 hours.
* `confirmation`: candidate 15-minute runs for the known anomalies and rare/100,
  approximately 7.4 hours. Select cases after seeing RSS slopes and coverage.

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

## Retained measurements

* Per-run configuration, binary SHA-256, source commit, dependency lockfile hash,
  environment snapshot, completed/offered/not-submitted counts, errors, achieved rate.
* User/system CPU, total CPU per command, actual peak outstanding commands and
  payload bytes. CPU belongs to the whole client process, including harness work.
* RSS at 250 ms, process high-water RSS, steady RSS over the final two minutes,
  average RSS, idle observation and connection-drop observation. The process high
  water includes warm-up; sampled measured peaks are reported separately. Shorter
  than 250 ms spikes can be missed by the time series, but the process high water
  still detects their maximum without precise timing.
* Per-payload-size HDR histograms (3 significant digits) and mean/p50/p99/p99.9/max
  for submission-to-completion, scheduled-to-completion and scheduling delay.
  Record sample count; suppress p99.9 when it equals max or has fewer than 10,000
  observations. Skipped offered requests are visible as not-submitted, not invented
  latency samples. Median run percentiles and pooled percentiles are distinct.
* Host CPU, memory, pressure, network bytes/errors/retransmissions each second;
  process status and smaps_rollup. Preserve raw counters for later delta analysis.
* Diagnostic requested-live/peak bytes and allocation counts; read/write observed
  capacity maxima, trim counts and read trims with incomplete versus complete
  decoded responses. BytesMut capacity can hide retained storage after advance.
  Allocation counters do not measure malloc rounding, fragmentation, libc internal
  realloc overlap, kernel socket buffers, or allocation stacks. Separate profiling
  is needed if these counters leave the cause unresolved.

## Build and run

Clone the repository, install the prerequisites, then build both source versions:

```sh
git clone https://github.com/ofirluzon/redis-rs-codec-bench.git
cd redis-rs-codec-bench
python3 scripts/build.py
python3 scripts/build.py --diagnostics
```

`bash scripts/bootstrap-linux.sh` is an optional setup helper for supported Linux distributions.
To run the loopback correctness checks, install `redis-server` and execute
`python3 scripts/local_check.py` after building both scored and diagnostic binaries.

Set `REDIS_URL` in the process environment. A local example is
`export REDIS_URL=redis://127.0.0.1:6379`. The URL is not saved as configuration in
results. Start a reviewed stage explicitly, for example:

```sh
python3 scripts/run_matrix.py plans/screening.json --results results/screening
python3 scripts/analyze.py results/screening
```

Each failed attempt stays separate. Only validated DONE attempts enter analysis.
Zero command errors are required; under 95% target is LIMITED rather than a false
success. Resuming on a different host/environment is rejected. Copy completed
results off-host after each stage, and periodically during long stages. Preserve
the entire kit (including Cargo.lock), raw histograms and telemetry in durable
local storage before terminating the instance. `scripts/backup_results.py` pulls
snapshots once or every configured interval (e.g. 600 seconds), using an SSH alias
supplied at runtime. It copies active attempts too; only DONE-validated attempts
enter analysis. Raw output can contain machine details, file paths or endpoint
information in diagnostic errors. Keep it private; share reviewed summaries rather
than committing a results directory. Start backups alongside long remote stages.

TTL, pooling and the single-connection exception are design candidates only, not
implemented binaries. First explain the existing regression. Implement any chosen
policy in an isolated experimental copy, with explicit byte/time bounds and its
own tests, and compare using the same frozen sources and environment.
