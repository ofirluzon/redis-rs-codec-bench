# Metrics and reports

## Generate and read the output

```sh
python3 scripts/analyze.py results/main
```

Only attempts whose `DONE.json` passes configuration, timing, count, telemetry and
checksum validation enter analysis. Failed/incomplete attempts remain on disk.
Analysis can run while a matrix is progressing; an incomplete report is labelled
**INCOMPLETE** and shows validated versus planned run counts. No traffic is started.

| File | What it displays |
| --- | --- |
| `report.md` | One comparison table per matching configuration. Overall latency and, for spike workloads, ordinary/spike groups. Each numeric cell shows every repetition followed by its arithmetic mean. Run status, total commands, achieved rate and full source/binary hashes follow each table. |
| `payload_report.md` | The same display split by exact payload size in bytes. |
| `per_run.csv` | Every collected/derived numeric metric, separately for each run and latency group. Includes configuration, seed, status and build identity. |
| `means.csv` | Arithmetic means across matched runs; `repetitions`, `limited_runs` and each numeric metric's `*_runs` column show contributing counts. |
| `medians.csv` | Medians across matched runs, preserving the alternative summary. |

See [the illustrative display](REPORT_EXAMPLE.md). Reports use **µs** for latency
and CPU per command, **seconds** for total CPU, and **MiB** for RSS. To express a
large-payload CPU cost in milliseconds, divide the µs value by 1,000. Unavailable
metrics display `—` and are blank in CSV. Missing runs display `missing run`;
completed runs without observations in that payload group display `no observations`.
A partially populated metric shows `Mean (n=N)` rather than implying all repetitions
contributed. `group_commands` counts only that latency group; `total_commands`
counts the entire run. Whole-process CPU/RSS repeat on each group row: **do not sum
those repeated values or attribute them to one payload size**.

### Repetitions and aggregation

Every default comparison/diagnostic stage runs each configuration **three times**.
Only the 30-second `small-capacity-pilot` uses one repetition. Configure this in
`matrix.json`, or generate a smaller/custom plan with `--repetitions N`.
The three versions are baseline, patched-off and patched-on. Their order rotates
between repetitions; each repetition uses the same seed across versions and a
different seed from the other repetitions. Generated IDs contain `r1`, `r2`, `r3`.
Custom IDs without that marker are labelled by their plan order within a version.

Equal measured duration does **not** imply equal completed commands. Compare
achieved rates and skip counts before interpreting CPU or latency differences.
Run statistics are given equal weight in the means; they are not weighted by
command count. The mean of three run p99 values is **not a pooled p99**. The same
caution applies to medians of run percentiles. Raw HDR distributions are retained
for a future pooled calculation; the analyzer does not pool across repetitions.

Only matching configuration and build identity aggregate: version/source/binary,
connections, concurrency limit, pipeline batch, workload/rate, measured/warm-up/idle
durations, workers, spike mode, diagnostic mode, allocator and latency group.
A missing payload group is excluded rather than treated as zero latency; contributing
counts reveal that exclusion. The means include `LIMITED` runs and their count;
filter these explicitly if the question requires a matched achieved rate.

## Command counts and CPU

| CSV metric | Definition / unit |
| --- | --- |
| `total_commands`, `group_commands` | Completed commands in the entire run / this latency group. |
| `offered` | Commands offered by the scheduler during measurement. |
| `capacity_skipped` | Offered commands skipped because the bounded in-flight budget was occupied. |
| `deadline_skipped` | Offered commands not submitted before the submission window ended. |
| `not_submitted` | `capacity_skipped + deadline_skipped`. On validated zero-error runs, `offered = total_commands + not_submitted`. |
| `target_commands_s`, `achieved_commands_s` | Configured target / completed commands divided by measured submission duration. Commands submitted inside the window can complete during drain. |
| `status`, `errors` | `OK` requires at least 95% of target; otherwise `LIMITED`. Deliberate single-spike/burst runs are `PROBE`, not throughput successes. Nonzero command errors reject the attempt. |
| `user_cpu_s`, `system_cpu_s`, `total_cpu_s` | Whole client process CPU between measurement start and final drain completion, excluding warm-up and later idle. Total is user + system. |
| `cpu_us_per_command` | `total_cpu_s * 1,000,000 / total_commands`. |
| `drain_s` | Time spent completing requests after the submission window. |
| `large_connections_covered`, `large_commands_min_per_connection` | Physical sockets that completed at least one payload ≥1 MiB, and the minimum number per socket. Raw summary retains the full per-connection counts. |
| `peak_inflight_commands`, `peak_inflight_payload_mib` | Atomic observed maxima of outstanding command count and request payload bytes. These count pipeline members, not batches; shared templates and allocator overhead are not in the payload gauge. |
| `average_inflight_commands`, `average_inflight_payload_mib` | Mean of the quarter-second samples during submission. |

CPU includes request construction, encoding/decoding, response checks/drop, scheduler,
sampling and histogram bookkeeping. It is not isolated codec CPU. The default
four-worker runtime can consume more than one CPU second per wall second. CPU per
command is process work, not elapsed response time. Report actual achieved rate
alongside it, especially when comparing saturated single-connection runs.

## Latency

All latency families retain HDR histograms with three significant digits and
`mean_us`, `p50_us`, `p99_us`, `p999_us`, `max_us` and `p999_reportable` in the raw
summary. CSV columns prepend the family name below. The mean is HDR's approximation
from quantized observations, not an exact sum of original nanosecond timings.

| Family | Interval |
| --- | --- |
| `response_*` | Task starts submitting a request → response validated and dropped. Includes command construction, client work, network, server and decode. This is the latency displayed in the main tables. |
| `scheduled_to_response_*` | Intended scheduled time → the same completion; includes scheduling delay. |
| `scheduling_*` | Intended scheduled time → task starts submitting. |

Sub-microsecond durations are recorded as 1 µs. Skipped commands have no latency
samples. For an explicit pipeline, **each member receives its batch's completion
latency**; individual reply arrival times are not measured. Rates/counts remain
commands, not pipeline batches.

`p999_us` is suppressed in CSV/reports when there are fewer than 10,000 observations
or it equals the histogram maximum. This rule applies separately to every latency
family/group/run. A rare 5 MiB group can therefore show p50/p99 and no p99.9 even
when the overall workload has many observations. `all` is a merged distribution
within one run; `ordinary` (<1 MiB) and `spike` (≥1 MiB) split rare/burst/single-spike
traffic. Exact payload sizes are also retained for every workload.

## Memory and observation windows

The client samples Linux resident memory every **250 ms**. The raw `samples.jsonl`
contains elapsed/unix time, phase, CPU totals, RSS, in-flight occupancy and optional
diagnostic counters. Phase 0 is warm-up; 1 is measurement plus drain; 2 is idle with
connections retained; 3 is two seconds after dropping connections; 4 is result
serialization. Short resident spikes may be missed by samples; process high-water
RSS still captures a maximum without precise timing.

| CSV metric | Window / meaning |
| --- | --- |
| `average_rss_mib` | Arithmetic mean of samples inside the measured submission window. |
| `steady_rss_mib` | Median RSS during its final `min(120, duration_s)` seconds. This label identifies a window; it does not establish that memory has stabilized. |
| `sampled_peak_rss_mib` | Maximum sample inside submission. This is **Peak RSS** in the Markdown tables. |
| `sampled_peak_including_drain_mib` | Maximum phase-1 sample, including later drain. |
| `process_peak_including_warmup_mib` | OS process high-water RSS read at measurement/drain completion; includes startup/warm-up, excludes later idle/drop/serialization. |
| `steady_rss_min_mib`, `steady_rss_max_mib` | Minimum/maximum in the steady-window samples. |
| `rss_slope_mib_per_min`, `steady_rss_slope_mib_per_min` | Least-squares RSS slope over submission / final steady window. Too few samples gives no slope. A slope alone proves neither a leak nor a plateau. |
| `idle_final_rss_mib` | Median of the final roughly five seconds of phase 2; blank if idle is disabled. |
| `after_drop_rss_mib` | Median of phase-3 samples, before result serialization. |

RSS includes shared payload templates, Rust/Tokio/histogram allocations and allocator
retention. Dropping a Rust buffer does not imply immediate RSS return to the OS.
Requested allocation bytes and RSS measure different things.

## Diagnostic counters

Diagnostic builds add atomic counters and track the system allocator. Use them to
explain behavior, **not to score the performance cost of the uninstrumented patch**.
CSV names prepend `alloc_` or `codec_`. `_end` is the level at measurement/drain end;
`_delta` is end minus start for event counters. Gauges/maxima have only `_end`.
Global maxima include startup and warm-up. Raw snapshots at start/end and samples
retain the original names below.

| Allocation counter | Meaning |
| --- | --- |
| `live_requested_bytes`, `peak_requested_bytes` | Currently live requested bytes / highest requested live total observed. |
| `allocations`, `frees`, `reallocations` | Successful allocation calls, deallocation calls, successful reallocation calls. Reallocations are separate from the other two counts. |
| `allocated_requested_bytes`, `freed_requested_bytes` | Requested-byte churn. A successful realloc counts its old size as freed and new size as allocated, even when resized in place. |

These track the **entire client**, not just the codec. They exclude malloc size
rounding, allocator fragmentation, internal realloc overlap, kernel socket buffers
and allocation stacks. Atomics are observational snapshots, not a transaction
across all counters.

| Codec counter | Meaning |
| --- | --- |
| `read_calls` | Decoder calls, not commands or network reads. One response may require multiple calls. |
| `read_trims_complete`, `read_trims_partial`, `write_trims` | Actual buffer replacements: read after a completed decode / after an incomplete decode, and write. Sum these for the number of shrinks. |
| `max_observed_read_len`, `max_observed_read_capacity`, `max_observed_write_capacity` | Largest observed length/capacity in bytes across codecs. |
| `read_observed_growths`, `write_observed_growths` | Events where observed capacity increased. Read compares decode entry with its previous exit; write compares before/after extending. The initial read observation compares with zero. |
| `read_observed_growth_bytes`, `write_observed_growth_bytes` | Sum of positive observed capacity changes, in bytes. |
| `trim_observed_capacity_bytes` | Sum of capacity observed immediately before replacement. |
| `replacement_requested_capacity_bytes` | Sum of requested replacement capacities (8 KiB per replacement in the pinned patch). |

Capacity changes do **not** count physical allocations or bytes returned to libc/OS.
`BytesMut::advance` can hide retained storage; reclaiming space can also change
observed capacity. Do not subtract replacement bytes from observed trimmed capacity
and call the difference reclaimed memory.

## Host and server telemetry

`host.jsonl` samples Linux `/proc` host CPU, memory, pressure, network and TCP counters,
plus process status and `smaps_rollup`, approximately every second. Analysis uses
samples within submission and differences between the first/last available samples;
`host_sample_span_s` gives the actual covered interval. Raw memory/pressure/status
remain in JSONL; they are not all converted to CSV summary columns.

* `host_cpu_*_ticks_delta`: host CPU time counters. `host_busy_pct` uses all host
  CPUs and treats idle + iowait as idle; guest time is not double counted. This is
  different from process CPU seconds.
* `net_<interface>_{rx,tx}_bytes_delta`, errors/drops: counters per interface.
  Loopback is retained, not presented as physical NIC throughput.
* `tcp_{RetransSegs,InErrs,OutRsts}_delta`: host-wide TCP counters. Other processes
  can contribute. Divide byte deltas by their sample span for bytes/s.
* Optional `--server-info`: endpoint INFO every five seconds in a separate process.
  `server_info_samples` and `server_info_sample_span_s` describe coverage.
  `endpoint_used_cpu_*_delta` (seconds) and `endpoint_total_*_delta` are cumulative
  counter changes; other `endpoint_*_final` fields retain final memory, clients,
  rejected connections and instantaneous ops/s. Missing data stay unavailable.

INFO covers **one endpoint**, not all shards/proxies/cluster nodes. It adds observer
load; its CPU and command counters can include INFO calls and unrelated traffic.
Without separate node telemetry, client-only measurements cannot establish a server
bottleneck. Sampling child CPU is not included in the harness's process CPU total.

## Raw artifacts and reproducibility

Each result root retains the frozen `plan.json`, `builds.json`, `environment.json`,
progress log, and a `kit/` snapshot of source, scripts, documentation, manifests,
lockfile and exact binaries. Each attempt retains configuration/build metadata,
`summary.json`, `samples.jsonl`, `host.jsonl`, command errors, stdout/stderr and
`latency-<group>-{response,scheduled,scheduling}.hdr`. Completed-attempt checksums
are in `DONE.json`. Reports and CSVs are derived outputs and can be regenerated;
keep raw artifacts private and durable. Partial attempts never contribute to means.
