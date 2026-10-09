# Illustrative report (fictional data)

**These numbers are invented to demonstrate the display. They are not benchmark results.**

Validated runs: **9/9**.

Each numeric cell lists the individual repetitions followed by their **arithmetic mean**. Means of run percentiles are not pooled percentiles. Missing observations are excluded from means; `n` shows the contributing count when it differs from the planned repetitions.

CPU and RSS belong to the whole client process and repeat on every latency-group row; **do not sum them across groups**. All latency and CPU/command values are µs; RSS is MiB. Steady RSS is the median of the last min(120, duration) seconds; peak here is the sampled submission-window peak. See [metric reference](METRICS.md) for other peaks, windows and diagnostic counters. Actual result directories retain this reference in the kit snapshot.

`LIMITED` means achieved rate below 95% of target. `PROBE` is a deliberate spike/burst observation, not a sustained-throughput success. `—` means unavailable or unreportable, including p99.9 when too few observations or equal to max.

## small · 1 connection(s) · target 50,000/s

In flight: 128 commands; pipeline batch: 1; workers: 4; measured/warm-up/idle: 300/30/30 s; spikes: random; allocator: system; diagnostics: False.

| Group | Version | Commands (group) | CPU s (whole run) | CPU µs/command (whole run) | Steady RSS MiB | Peak RSS MiB | Latency mean µs | p50 µs | p99 µs | p99.9 µs |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| all | baseline (3/3) | r1: 15,000,000<br>r2: 15,000,000<br>r3: 15,000,000<br>**Mean: 15,000,000** | r1: 150.000<br>r2: 150.000<br>r3: 600.000<br>**Mean: 300.000** | r1: 10.00<br>r2: 10.00<br>r3: 40.00<br>**Mean: 20.00** | r1: 10.00<br>r2: 10.00<br>r3: 10.00<br>**Mean: 10.00** | r1: 12.00<br>r2: 12.00<br>r3: 12.00<br>**Mean: 12.00** | r1: 20.00<br>r2: 20.00<br>r3: 20.00<br>**Mean: 20.00** | r1: 15.00<br>r2: 15.00<br>r3: 15.00<br>**Mean: 15.00** | r1: 40.00<br>r2: 40.00<br>r3: 40.00<br>**Mean: 40.00** | r1: —<br>r2: —<br>r3: —<br>**Mean: —** |
| all | patched-off (3/3) | r1: 15,000,000<br>r2: 15,000,000<br>r3: 15,000,000<br>**Mean: 15,000,000** | r1: 151.500<br>r2: 151.500<br>r3: 606.000<br>**Mean: 303.000** | r1: 10.10<br>r2: 10.10<br>r3: 40.40<br>**Mean: 20.20** | r1: 10.00<br>r2: 10.00<br>r3: 10.00<br>**Mean: 10.00** | r1: 12.00<br>r2: 12.00<br>r3: 12.00<br>**Mean: 12.00** | r1: 20.00<br>r2: 20.00<br>r3: 20.00<br>**Mean: 20.00** | r1: 15.00<br>r2: 15.00<br>r3: 15.00<br>**Mean: 15.00** | r1: 40.00<br>r2: 40.00<br>r3: 40.00<br>**Mean: 40.00** | r1: —<br>r2: —<br>r3: —<br>**Mean: —** |
| all | patched-on (3/3) | r1: 15,000,000<br>r2: 15,000,000<br>r3: 15,000,000<br>**Mean: 15,000,000** | r1: 153.000<br>r2: 153.000<br>r3: 612.000<br>**Mean: 306.000** | r1: 10.20<br>r2: 10.20<br>r3: 40.80<br>**Mean: 20.40** | r1: 10.00<br>r2: 10.00<br>r3: 10.00<br>**Mean: 10.00** | r1: 12.00<br>r2: 12.00<br>r3: 12.00<br>**Mean: 12.00** | r1: 20.00<br>r2: 20.00<br>r3: 20.00<br>**Mean: 20.00** | r1: 15.00<br>r2: 15.00<br>r3: 15.00<br>**Mean: 15.00** | r1: 40.00<br>r2: 40.00<br>r3: 40.00<br>**Mean: 40.00** | r1: —<br>r2: —<br>r3: —<br>**Mean: —** |

### Run status and build identity

| Version | Repetition | Total commands | Achieved commands/s | Status | Errors | Source / binary SHA-256 |
| --- | --- | ---: | ---: | --- | ---: | --- |
| baseline | r1 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| baseline | r2 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| baseline | r3 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| patched-off | r1 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| patched-off | r2 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| patched-off | r3 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| patched-on | r1 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| patched-on | r2 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |
| patched-on | r3 | 15,000,000 | 50,000.00 | OK | 0 | `fixture` / `fixture` |

