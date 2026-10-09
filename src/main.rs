use hdrhistogram::{
    Histogram,
    serialization::{Serializer, V2Serializer},
};
use redis::aio::MultiplexedConnection;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{BufWriter, Write},
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};
use tokio::{
    sync::{OwnedSemaphorePermit, Semaphore},
    task::JoinSet,
};

#[cfg(all(feature = "alloc-diagnostics", feature = "jemalloc"))]
compile_error!("Allocation diagnostics and jemalloc are separate experiments.");
#[cfg(feature = "jemalloc")]
#[global_allocator]
static ALLOCATOR: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

// Requested bytes, not malloc usable bytes or RSS. Diagnostic builds only.
#[cfg(feature = "alloc-diagnostics")]
mod allocation {
    use std::{
        alloc::{GlobalAlloc, Layout, System},
        sync::atomic::{AtomicU64, Ordering},
    };
    pub struct Tracking;
    pub static LIVE: AtomicU64 = AtomicU64::new(0);
    pub static PEAK: AtomicU64 = AtomicU64::new(0);
    pub static ALLOCS: AtomicU64 = AtomicU64::new(0);
    pub static FREES: AtomicU64 = AtomicU64::new(0);
    pub static REALLOCS: AtomicU64 = AtomicU64::new(0);
    pub static ALLOCATED: AtomicU64 = AtomicU64::new(0);
    pub static FREED: AtomicU64 = AtomicU64::new(0);
    fn add(n: usize) {
        let now = LIVE.fetch_add(n as u64, Ordering::Relaxed) + n as u64;
        PEAK.fetch_max(now, Ordering::Relaxed);
    }
    unsafe impl GlobalAlloc for Tracking {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            let p = unsafe { System.alloc(layout) };
            if !p.is_null() {
                add(layout.size());
                ALLOCATED.fetch_add(layout.size() as u64, Ordering::Relaxed);
                ALLOCS.fetch_add(1, Ordering::Relaxed);
            }
            p
        }
        unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
            let p = unsafe { System.alloc_zeroed(layout) };
            if !p.is_null() {
                add(layout.size());
                ALLOCATED.fetch_add(layout.size() as u64, Ordering::Relaxed);
                ALLOCS.fetch_add(1, Ordering::Relaxed);
            }
            p
        }
        unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
            unsafe { System.dealloc(p, layout) };
            LIVE.fetch_sub(layout.size() as u64, Ordering::Relaxed);
            FREES.fetch_add(1, Ordering::Relaxed);
            FREED.fetch_add(layout.size() as u64, Ordering::Relaxed);
        }
        unsafe fn realloc(&self, p: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
            let new_p = unsafe { System.realloc(p, layout, new_size) };
            if !new_p.is_null() {
                if new_size >= layout.size() {
                    add(new_size - layout.size());
                } else {
                    LIVE.fetch_sub((layout.size() - new_size) as u64, Ordering::Relaxed);
                }
                REALLOCS.fetch_add(1, Ordering::Relaxed);
                ALLOCATED.fetch_add(new_size as u64, Ordering::Relaxed);
                FREED.fetch_add(layout.size() as u64, Ordering::Relaxed);
            }
            new_p
        }
    }
}
#[cfg(feature = "alloc-diagnostics")]
#[global_allocator]
static ALLOCATOR: allocation::Tracking = allocation::Tracking;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Config {
    id: String,
    variant: String,
    workload: String,
    connections: usize,
    per_connection_inflight: usize,
    rate: u64, // aggregate commands/s, including pipeline members
    duration_s: u64,
    #[serde(default = "warmup_default")]
    warmup_s: u64,
    #[serde(default = "idle_default")]
    idle_s: u64,
    #[serde(default = "workers_default")]
    workers: usize,
    #[serde(default = "batch_default")]
    pipeline_batch: usize,
    #[serde(default)]
    seed: u64,
    #[serde(default = "mode_default")]
    spike_mode: String,
}
fn warmup_default() -> u64 {
    30
}
fn idle_default() -> u64 {
    30
}
fn workers_default() -> usize {
    4
}
fn batch_default() -> usize {
    1
}
fn mode_default() -> String {
    "random".into()
}

fn mixed(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e3779b97f4a7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
    x ^ (x >> 31)
}
fn sizes(workload: &str) -> Result<Vec<usize>, String> {
    match workload {
        "small" => Ok(vec![128, 512, 1024, 4096]),
        "rare" | "burst" | "single-spike" => Ok(vec![4096, 5 * 1024 * 1024]),
        "near" => Ok([8, 24, 40, 56, 72, 88, 104, 120]
            .into_iter()
            .map(|n| n * 1024)
            .collect()),
        "large" => Ok(vec![4 * 1024 * 1024, 5 * 1024 * 1024, 6 * 1024 * 1024]),
        _ => Err(format!("Unknown workload {workload}")),
    }
}
fn size_index(config: &Config, seq: u64, pool_len: usize) -> usize {
    if matches!(config.workload.as_str(), "rare" | "burst" | "single-spike") {
        let large = if config.workload == "single-spike" {
            seq == config.rate * 5
        } else if config.workload == "burst" {
            seq % (config.rate * 20) < config.rate
        } else if config.spike_mode == "synchronized" {
            (seq / config.connections as u64) % 1000 == 999
        } else {
            mixed(seq ^ config.seed).is_multiple_of(1000)
        };
        usize::from(large)
    } else {
        (mixed(seq ^ config.seed) % pool_len as u64) as usize
    }
}

#[derive(Default)]
struct Gauges {
    commands: AtomicU64,
    bytes: AtomicU64,
    peak_commands: AtomicU64,
    peak_bytes: AtomicU64,
}
struct Flight {
    gauges: Arc<Gauges>,
    commands: u64,
    bytes: u64,
    _permit: OwnedSemaphorePermit,
}
impl Flight {
    fn new(gauges: Arc<Gauges>, commands: u64, bytes: u64, permit: OwnedSemaphorePermit) -> Self {
        let c = gauges.commands.fetch_add(commands, Ordering::Relaxed) + commands;
        let b = gauges.bytes.fetch_add(bytes, Ordering::Relaxed) + bytes;
        gauges.peak_commands.fetch_max(c, Ordering::Relaxed);
        gauges.peak_bytes.fetch_max(b, Ordering::Relaxed);
        Self {
            gauges,
            commands,
            bytes,
            _permit: permit,
        }
    }
}
impl Drop for Flight {
    fn drop(&mut self) {
        self.gauges
            .commands
            .fetch_sub(self.commands, Ordering::Relaxed);
        self.gauges.bytes.fetch_sub(self.bytes, Ordering::Relaxed);
    }
}
struct Completion {
    submitted: Instant,
    completed: Instant,
    scheduled: Vec<Instant>,
    sizes: Vec<usize>,
    connection: usize,
    error: Option<String>,
}
async fn request(
    mut conn: MultiplexedConnection,
    payloads: Vec<Arc<Vec<u8>>>,
    scheduled: Vec<Instant>,
    connection: usize,
    flight: Flight,
) -> Completion {
    let submitted = Instant::now();
    let sizes = payloads.iter().map(|p| p.len()).collect();
    let outcome: redis::RedisResult<Vec<Vec<u8>>> = if payloads.len() == 1 {
        redis::cmd("ECHO")
            .arg(payloads[0].as_slice())
            .query_async::<Vec<u8>>(&mut conn)
            .await
            .map(|r| vec![r])
    } else {
        let mut pipeline = redis::pipe();
        for payload in &payloads {
            pipeline.cmd("ECHO").arg(payload.as_slice());
        }
        pipeline.query_async(&mut conn).await
    };
    let error = match outcome {
        Ok(responses) if responses.len() == payloads.len() => {
            responses.iter().zip(&payloads).find_map(|(r, p)| {
                let marker_len = 16.min(p.len());
                if r.len() != p.len()
                    || r[..marker_len] != p[..marker_len]
                    || r[r.len() - marker_len..] != p[p.len() - marker_len..]
                {
                    Some("ECHO length/marker mismatch".to_string())
                } else {
                    None
                }
            })
        }
        Ok(_) => Some("Wrong pipeline response count".into()),
        Err(e) => Some(e.to_string()),
    };
    let completed = Instant::now();
    // Responses have been dropped by the match above; Completion retains no payload bytes.
    drop(flight);
    Completion {
        submitted,
        completed,
        scheduled,
        sizes,
        connection,
        error,
    }
}

struct Latencies {
    service: Histogram<u64>,
    end_to_end: Histogram<u64>,
    scheduling: Histogram<u64>,
    commands: u64,
    bytes: u64,
}
impl Latencies {
    fn new() -> Self {
        Self {
            service: Histogram::new(3).unwrap(),
            end_to_end: Histogram::new(3).unwrap(),
            scheduling: Histogram::new(3).unwrap(),
            commands: 0,
            bytes: 0,
        }
    }
}
#[derive(Default)]
struct Stats {
    groups: BTreeMap<usize, Latencies>,
    completed: u64,
    bytes: u64,
    errors: u64,
    offered: u64,
    not_submitted: u64,
    capacity_skipped: u64,
    deadline_skipped: u64,
    per_connection_large: Vec<u64>,
}
fn record(
    stats: &mut Stats,
    r: Completion,
    errors: &mut BufWriter<File>,
    epoch: Instant,
) -> Result<(), String> {
    if let Some(error) = r.error {
        stats.errors += r.sizes.len() as u64;
        writeln!(
            errors,
            "{}",
            json!({"elapsed_s":epoch.elapsed().as_secs_f64(), "connection":r.connection,
            "commands":r.sizes.len(), "error":error})
        )
        .map_err(|e| e.to_string())?;
        return Err("Command error; run invalidated, see errors.jsonl".into());
    }
    for (size, scheduled) in r.sizes.into_iter().zip(r.scheduled) {
        let group = stats.groups.entry(size).or_insert_with(Latencies::new);
        let service = r.completed.duration_since(r.submitted).as_micros() as u64;
        let end_to_end = r.completed.duration_since(scheduled).as_micros() as u64;
        let scheduling = r.submitted.duration_since(scheduled).as_micros() as u64;
        group
            .service
            .record(service.max(1))
            .map_err(|e| e.to_string())?;
        group
            .end_to_end
            .record(end_to_end.max(1))
            .map_err(|e| e.to_string())?;
        group
            .scheduling
            .record(scheduling.max(1))
            .map_err(|e| e.to_string())?;
        group.commands += 1;
        group.bytes += size as u64;
        stats.completed += 1;
        stats.bytes += size as u64;
        if size >= 1024 * 1024 {
            stats.per_connection_large[r.connection] += 1;
        }
    }
    Ok(())
}

fn due_batch(seq: u64, due: u64, maximum: u64, batch: usize) -> Option<usize> {
    let count = (maximum.saturating_sub(seq)).min(batch as u64);
    (count > 0 && seq.saturating_add(count) <= due).then_some(count as usize)
}

fn diagnostics() -> serde_json::Value {
    #[allow(unused_mut)]
    let mut result = json!({});
    #[cfg(feature = "alloc-diagnostics")]
    {
        result["alloc"] = json!({"live_requested_bytes":allocation::LIVE.load(Ordering::Relaxed),
        "peak_requested_bytes":allocation::PEAK.load(Ordering::Relaxed),
        "allocations":allocation::ALLOCS.load(Ordering::Relaxed),"frees":allocation::FREES.load(Ordering::Relaxed),
        "reallocations":allocation::REALLOCS.load(Ordering::Relaxed),
        "allocated_requested_bytes":allocation::ALLOCATED.load(Ordering::Relaxed),
        "freed_requested_bytes":allocation::FREED.load(Ordering::Relaxed)});
    }
    #[cfg(feature = "codec-diagnostics")]
    {
        result["codec"] = serde_json::to_value(redis::codec_bench_metrics::snapshot()).unwrap();
    }
    result
}

async fn phase(
    config: &Config,
    seconds: u64,
    conns: &[MultiplexedConnection],
    payloads: &[Arc<Vec<u8>>],
    gauges: Arc<Gauges>,
    errors: &mut BufWriter<File>,
    epoch: Instant,
) -> Result<(Stats, f64), String> {
    let start = Instant::now();
    let end = start + Duration::from_secs(seconds);
    let maximum = if config.workload == "single-spike" {
        (seconds * config.rate).min(config.rate * 5 + 1)
    } else {
        seconds * config.rate
    };
    let mut seq = 0;
    let permits: Vec<_> = conns
        .iter()
        .map(|_| Arc::new(Semaphore::new(config.per_connection_inflight)))
        .collect();
    let mut running = JoinSet::new();
    let mut stats = Stats {
        per_connection_large: vec![0; config.connections],
        ..Stats::default()
    };
    let mut clock = tokio::time::interval(Duration::from_millis(1));
    clock.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    while Instant::now() < end {
        tokio::select! {
            _ = clock.tick() => {
                let due = ((start.elapsed().as_secs_f64() * config.rate as f64) as u64).saturating_add(1).min(maximum);
                // Bounded offered load: unavailable slots are counted, not accumulated in an unbounded queue.
                while let Some(batch_size) = due_batch(seq, due, maximum, config.pipeline_batch) {
                    if Instant::now() >= end { break; }
                    let conn_index = ((seq / config.pipeline_batch as u64) % conns.len() as u64) as usize;
                    stats.offered += batch_size as u64;
                    let Ok(permit) = permits[conn_index].clone().try_acquire_many_owned(batch_size as u32) else {
                        stats.not_submitted += batch_size as u64;
                        stats.capacity_skipped += batch_size as u64;
                        seq += batch_size as u64;
                        continue;
                    };
                    let mut batch = Vec::with_capacity(batch_size);
                    let mut scheduled = Vec::with_capacity(batch_size);
                    for i in 0..batch_size as u64 {
                        batch.push(payloads[size_index(config, seq+i, payloads.len())].clone());
                        scheduled.push(start + Duration::from_secs_f64((seq+i) as f64 / config.rate as f64));
                    }
                    let bytes = batch.iter().map(|p|p.len() as u64).sum();
                    let flight = Flight::new(gauges.clone(), batch_size as u64, bytes, permit);
                    running.spawn(request(conns[conn_index].clone(), batch, scheduled, conn_index, flight));
                    seq += batch_size as u64;
                }
            }
            Some(result) = running.join_next(), if !running.is_empty() => {
                let r = result.map_err(|e|e.to_string())?;
                record(&mut stats,r,errors,epoch)?;
            }
        }
    }
    // Explicit drain time. Commands started in the window belong to that window even if they finish later.
    stats.deadline_skipped = maximum.saturating_sub(stats.offered);
    stats.not_submitted += stats.deadline_skipped;
    stats.offered = maximum;
    while let Some(result) = running.join_next().await {
        record(
            &mut stats,
            result.map_err(|e| e.to_string())?,
            errors,
            epoch,
        )?;
    }
    let drain_s = Instant::now().saturating_duration_since(end).as_secs_f64();
    Ok((stats, drain_s))
}

fn usage() -> (f64, f64, u64) {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::zeroed();
    let ok = unsafe { libc::getrusage(libc::RUSAGE_SELF, usage.as_mut_ptr()) };
    assert_eq!(ok, 0);
    let usage = unsafe { usage.assume_init() };
    let user = usage.ru_utime.tv_sec as f64 + usage.ru_utime.tv_usec as f64 / 1e6;
    let system = usage.ru_stime.tv_sec as f64 + usage.ru_stime.tv_usec as f64 / 1e6;
    let peak = usage.ru_maxrss as u64;
    #[cfg(not(target_os = "macos"))]
    let peak = peak * 1024;
    (user, system, peak)
}
fn sample_rss() -> Option<u64> {
    #[cfg(target_os = "linux")]
    {
        let statm = fs::read_to_string("/proc/self/statm").ok()?;
        let pages: u64 = statm.split_whitespace().nth(1)?.parse().ok()?;
        Some(pages * unsafe { libc::sysconf(libc::_SC_PAGESIZE) } as u64)
    }
    #[cfg(not(target_os = "linux"))]
    {
        None
    }
}
fn collector(
    out: PathBuf,
    epoch: Instant,
    stop: Arc<AtomicBool>,
    gauges: Arc<Gauges>,
    phase: Arc<AtomicU64>,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut writer = BufWriter::new(File::create(out).unwrap());
        while !stop.load(Ordering::Relaxed) {
            let (user, system, peak) = usage();
            let sample = json!({"elapsed_s":epoch.elapsed().as_secs_f64(),"unix_s":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64(),"phase":phase.load(Ordering::Relaxed),
                "rss_bytes":sample_rss(),"process_peak_rss_bytes":peak,"user_cpu_s":user,"system_cpu_s":system,
                "inflight_commands":gauges.commands.load(Ordering::Relaxed),"inflight_payload_bytes":gauges.bytes.load(Ordering::Relaxed)});
            let mut sample = sample;
            if let Some(metrics) = diagnostics().as_object() {
                for (name, value) in metrics {
                    sample[name] = value.clone();
                }
            }
            writeln!(writer, "{sample}").unwrap();
            writer.flush().unwrap();
            std::thread::sleep(Duration::from_millis(250));
        }
    })
}
fn histogram_summary(hist: &Histogram<u64>) -> serde_json::Value {
    json!({"count":hist.len(),"mean_us":hist.mean(),"p50_us":hist.value_at_quantile(0.5),
        "p99_us":hist.value_at_quantile(0.99),"p999_us":hist.value_at_quantile(0.999),"max_us":hist.max(),
        "p999_reportable":hist.len() >= 10_000 && hist.value_at_quantile(0.999) != hist.max()})
}
fn save_group(
    out: &std::path::Path,
    name: &str,
    lat: &Latencies,
    pipeline: bool,
) -> Result<serde_json::Value, String> {
    for (kind, hist) in [
        ("response", &lat.service),
        ("scheduled", &lat.end_to_end),
        ("scheduling", &lat.scheduling),
    ] {
        let mut file = File::create(out.join(format!("latency-{name}-{kind}.hdr")))
            .map_err(|e| e.to_string())?;
        V2Serializer::new()
            .serialize(hist, &mut file)
            .map_err(|e| e.to_string())?;
    }
    Ok(
        json!({"commands":lat.commands,"bytes":lat.bytes,"response":histogram_summary(&lat.service),
        "scheduled_to_response":histogram_summary(&lat.end_to_end),"scheduling":histogram_summary(&lat.scheduling),
        "pipeline_members_share_batch_latency":pipeline}),
    )
}
fn merge_group(target: &mut Latencies, source: &Latencies) -> Result<(), String> {
    target
        .service
        .add(&source.service)
        .map_err(|e| e.to_string())?;
    target
        .end_to_end
        .add(&source.end_to_end)
        .map_err(|e| e.to_string())?;
    target
        .scheduling
        .add(&source.scheduling)
        .map_err(|e| e.to_string())?;
    target.commands += source.commands;
    target.bytes += source.bytes;
    Ok(())
}

async fn run(config: Config, out: PathBuf) -> Result<(), String> {
    fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let url = std::env::var("REDIS_URL")
        .map_err(|_| "REDIS_URL must be set; credentials are not written to results".to_string())?;
    let client = redis::Client::open(url).map_err(|e| e.to_string())?;
    #[cfg(feature = "patched")]
    let connection_config = redis::AsyncConnectionConfig::new()
        .set_codec_buffer_trim_threshold(if config.variant == "patched-off" {
            None
        } else {
            std::num::NonZeroUsize::new(64 * 1024)
        })
        .set_response_timeout(Some(Duration::from_secs(30)));
    #[cfg(not(feature = "patched"))]
    let connection_config =
        redis::AsyncConnectionConfig::new().set_response_timeout(Some(Duration::from_secs(30)));
    let mut conns = Vec::new();
    for _ in 0..config.connections {
        conns.push(
            client
                .get_multiplexed_async_connection_with_config(&connection_config)
                .await
                .map_err(|e| e.to_string())?,
        );
    }
    let payloads: Vec<_> = sizes(&config.workload)?
        .into_iter()
        .map(|size| Arc::new(vec![0x5a; size]))
        .collect();
    let epoch = Instant::now();
    let epoch_unix_s = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let gauges = Arc::new(Gauges::default());
    let phase_id = Arc::new(AtomicU64::new(0)); // 0=warmup, 1=measured, 2=idle, 3=connections dropped, 4=result serialization
    let stop = Arc::new(AtomicBool::new(false));
    let sampler = collector(
        out.join("samples.jsonl"),
        epoch,
        stop.clone(),
        gauges.clone(),
        phase_id.clone(),
    );
    let mut errors =
        BufWriter::new(File::create(out.join("errors.jsonl")).map_err(|e| e.to_string())?);
    let result = async {
        if config.warmup_s > 0 { phase(&config,config.warmup_s,&conns,&payloads,gauges.clone(),&mut errors,epoch).await?; }
        let (start_user,start_system,_) = usage();
        let diagnostic_start = diagnostics();
        gauges.peak_commands.store(0,Ordering::Relaxed); gauges.peak_bytes.store(0,Ordering::Relaxed);
        phase_id.store(1,Ordering::Relaxed);
        let measure_start_s = epoch.elapsed().as_secs_f64();
        let (stats,drain_s) = phase(&config,config.duration_s,&conns,&payloads,gauges.clone(),&mut errors,epoch).await?;
        let measure_end_s = epoch.elapsed().as_secs_f64();
        let (end_user,end_system,process_peak_rss) = usage();
        let diagnostic_end = diagnostics();
        phase_id.store(2,Ordering::Relaxed);
        tokio::time::sleep(Duration::from_secs(config.idle_s)).await;
        drop(conns);
        phase_id.store(3,Ordering::Relaxed);
        tokio::time::sleep(Duration::from_secs(2)).await;
        phase_id.store(4,Ordering::Relaxed); // Keep report allocations out of drop observations.
        let mut groups = BTreeMap::new();
        let mut aggregates = BTreeMap::new();
        for (size, lat) in &stats.groups {
            groups.insert(size.to_string(), save_group(&out, &size.to_string(), lat, config.pipeline_batch > 1)?);
            merge_group(aggregates.entry("all").or_insert_with(Latencies::new), lat)?;
            if matches!(config.workload.as_str(), "rare" | "burst" | "single-spike") {
                let name = if *size < 1024 * 1024 { "ordinary" } else { "spike" };
                merge_group(aggregates.entry(name).or_insert_with(Latencies::new), lat)?;
            }
        }
        let mut aggregate_groups = BTreeMap::new();
        for (name, lat) in &aggregates {
            aggregate_groups.insert(*name, save_group(&out, name, lat, config.pipeline_batch > 1)?);
        }
        if config.workload == "single-spike" && stats.per_connection_large.iter().sum::<u64>() != 1 {
            return Err("Single spike was skipped; probe invalid".into());
        }
        let rate = stats.completed as f64/config.duration_s as f64;
        let summary = json!({"schema":2,"config":config,"build_commit":option_env!("CODEC_BENCH_COMMIT").unwrap_or("unrecorded-test-build"),
            "patched_api":cfg!(feature="patched"),"allocator":if cfg!(feature="jemalloc") {"jemalloc"} else {"system"},
            "allocation_diagnostics":cfg!(feature="alloc-diagnostics"),"codec_diagnostics":cfg!(feature="codec-diagnostics"),
            "epoch_unix_s":epoch_unix_s,"pacing_tick_us":1000,"measure_start_s":measure_start_s,"measure_end_s":measure_end_s,
            "submit_window_s":config.duration_s,"drain_s":drain_s,"completed":stats.completed,"offered":stats.offered,
            "not_submitted":stats.not_submitted,"capacity_skipped":stats.capacity_skipped,"deadline_skipped":stats.deadline_skipped,"errors":stats.errors,"achieved_commands_s":rate,
            "status":if matches!(config.workload.as_str(),"single-spike"|"burst") {"PROBE"} else if rate >= config.rate as f64*0.95 {"OK"} else {"LIMITED"},
            "payload_bytes_each_direction":stats.bytes,"user_cpu_s":end_user-start_user,"system_cpu_s":end_system-start_system,
            "cpu_us_per_command":if stats.completed>0 {(end_user-start_user+end_system-start_system)*1e6/stats.completed as f64} else {0.0},
            "process_peak_rss_bytes_includes_warmup":process_peak_rss,
            "peak_inflight_commands":gauges.peak_commands.load(Ordering::Relaxed),
            "peak_inflight_payload_bytes":gauges.peak_bytes.load(Ordering::Relaxed),
            "large_commands_per_connection":stats.per_connection_large,"groups":groups,"aggregate_groups":aggregate_groups,
            "diagnostic_start":diagnostic_start,"diagnostic_end":diagnostic_end});
        fs::write(out.join("summary.json"),serde_json::to_vec_pretty(&summary).unwrap()).map_err(|e|e.to_string())?;
        Ok(())
    }.await;
    stop.store(true, Ordering::Relaxed);
    sampler
        .join()
        .map_err(|_| "RSS sampler failed".to_string())?;
    errors.flush().map_err(|e| e.to_string())?;
    result
}
fn main() {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("Usage: codec-bench CONFIG.json OUTPUT_DIRECTORY (REDIS_URL in environment)");
        std::process::exit(2);
    }
    let config: Config = match fs::read(&args[1])
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
    {
        Some(c) => c,
        None => {
            eprintln!("Invalid config");
            std::process::exit(2)
        }
    };
    let valid_variant = if cfg!(feature = "patched") {
        matches!(config.variant.as_str(), "patched-off" | "patched-on")
    } else {
        config.variant == "baseline"
    };
    if !valid_variant
        || config.connections == 0
        || config.workers == 0
        || config.rate == 0
        || config.duration_s == 0
        || config.pipeline_batch == 0
        || config.pipeline_batch > config.per_connection_inflight
        || config.per_connection_inflight > u32::MAX as usize
        || config
            .rate
            .checked_mul(config.duration_s.max(config.warmup_s).max(20))
            .is_none()
        || config
            .connections
            .checked_mul(config.per_connection_inflight)
            .is_none()
        || (config.workload == "single-spike"
            && (config.duration_s <= 5
                || config.warmup_s != 0
                || config.connections != 1
                || config.pipeline_batch != 1))
        || sizes(&config.workload).is_err()
        || !matches!(config.spike_mode.as_str(), "random" | "synchronized")
    {
        eprintln!("Invalid config or binary/variant mismatch");
        std::process::exit(2);
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(config.workers)
        .enable_all()
        .build()
        .unwrap();
    if let Err(error) = runtime.block_on(run(config, PathBuf::from(&args[2]))) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config() -> Config {
        Config {
            id: "test".into(),
            variant: "baseline".into(),
            workload: "rare".into(),
            connections: 100,
            per_connection_inflight: 1,
            rate: 2000,
            duration_s: 300,
            warmup_s: 0,
            idle_s: 0,
            workers: 4,
            pipeline_batch: 1,
            seed: 42,
            spike_mode: "random".into(),
        }
    }
    #[test]
    fn pipeline_tail_and_due_time() {
        assert_eq!(due_batch(96, 100, 100, 4), Some(4));
        assert_eq!(due_batch(100, 102, 103, 4), None);
        assert_eq!(due_batch(100, 103, 103, 4), Some(3));
        assert_eq!(due_batch(103, 103, 103, 4), None);
    }
    #[test]
    fn merged_latency_is_not_mean_of_percentiles() {
        let mut a = Latencies::new();
        let mut b = Latencies::new();
        a.service.record_n(10, 99).unwrap();
        b.service.record(1000).unwrap();
        merge_group(&mut a, &b).unwrap();
        assert_eq!(a.service.len(), 100);
        assert_eq!(a.service.value_at_quantile(0.5), 10);
    }
    #[test]
    fn rare_distribution_and_connection_coverage() {
        let c = config();
        let mut counts = vec![0; 100];
        for seq in 0..1_200_000 {
            if size_index(&c, seq, 2) == 1 {
                counts[seq as usize % 100] += 1;
            }
        }
        let total: usize = counts.iter().sum();
        assert!((1000..1400).contains(&total));
        assert!(counts.iter().all(|n| *n > 0));
    }
    #[test]
    fn near_payloads_average_exactly_64_kib() {
        let values = sizes("near").unwrap();
        assert_eq!(values.iter().sum::<usize>() / values.len(), 64 * 1024);
    }
    #[test]
    fn flight_gauges_release_with_permit() {
        let gauges = Arc::new(Gauges::default());
        let permits = Arc::new(Semaphore::new(16));
        let flight = Flight::new(
            gauges.clone(),
            4,
            1024,
            permits.clone().try_acquire_many_owned(4).unwrap(),
        );
        assert_eq!(permits.available_permits(), 12);
        drop(flight);
        assert_eq!(gauges.commands.load(Ordering::Relaxed), 0);
        assert_eq!(gauges.bytes.load(Ordering::Relaxed), 0);
        assert_eq!(permits.available_permits(), 16);
    }
}
