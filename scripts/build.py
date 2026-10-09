#!/usr/bin/env python3
"""Build matched pinned sources. Changes are confined to this standalone kit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
COMMITS = {"baseline": "018df9b148fa4c1c4ecc1050f9fb10df8a32cddf",
           "patched": "7bb03646438ae6abd116be652932bf4f7b822a37"}

METRICS = '''use std::{collections::BTreeMap, sync::atomic::{AtomicU64, Ordering}};
static READ_CALLS: AtomicU64 = AtomicU64::new(0);
static MAX_READ_LEN: AtomicU64 = AtomicU64::new(0);
static MAX_READ_CAP: AtomicU64 = AtomicU64::new(0);
static MAX_WRITE_CAP: AtomicU64 = AtomicU64::new(0);
static READ_TRIMS_COMPLETE: AtomicU64 = AtomicU64::new(0);
static READ_TRIMS_PARTIAL: AtomicU64 = AtomicU64::new(0);
static WRITE_TRIMS: AtomicU64 = AtomicU64::new(0);
static READ_GROWTHS: AtomicU64 = AtomicU64::new(0);
static WRITE_GROWTHS: AtomicU64 = AtomicU64::new(0);
static READ_GROWTH_BYTES: AtomicU64 = AtomicU64::new(0);
static WRITE_GROWTH_BYTES: AtomicU64 = AtomicU64::new(0);
static TRIM_OBSERVED_CAPACITY: AtomicU64 = AtomicU64::new(0);
static REPLACEMENT_CAPACITY: AtomicU64 = AtomicU64::new(0);
pub fn read(len: usize, capacity: usize) {
    READ_CALLS.fetch_add(1, Ordering::Relaxed);
    MAX_READ_LEN.fetch_max(len as u64, Ordering::Relaxed);
    MAX_READ_CAP.fetch_max(capacity as u64, Ordering::Relaxed);
}
pub fn growth(read: bool, before: usize, after: usize) {
    if after > before {
        let (count, bytes) = if read { (&READ_GROWTHS, &READ_GROWTH_BYTES) } else { (&WRITE_GROWTHS, &WRITE_GROWTH_BYTES) };
        count.fetch_add(1, Ordering::Relaxed); bytes.fetch_add((after-before) as u64, Ordering::Relaxed);
    }
}
pub fn write(before: usize, capacity: usize) {
    MAX_WRITE_CAP.fetch_max(capacity as u64, Ordering::Relaxed); growth(false, before, capacity);
}
pub fn replacement(before: usize, after: usize) {
    TRIM_OBSERVED_CAPACITY.fetch_add(before as u64, Ordering::Relaxed);
    REPLACEMENT_CAPACITY.fetch_add(after as u64, Ordering::Relaxed);
}
pub fn read_trim(complete: bool) {
    let counter = if complete { &READ_TRIMS_COMPLETE } else { &READ_TRIMS_PARTIAL };
    counter.fetch_add(1, Ordering::Relaxed);
}
pub fn write_trim() { WRITE_TRIMS.fetch_add(1, Ordering::Relaxed); }
pub fn snapshot() -> BTreeMap<&'static str, u64> {
    [("read_calls", &READ_CALLS), ("max_observed_read_len", &MAX_READ_LEN),
    ("max_observed_read_capacity", &MAX_READ_CAP), ("max_observed_write_capacity", &MAX_WRITE_CAP),
    ("read_trims_complete", &READ_TRIMS_COMPLETE), ("read_trims_partial", &READ_TRIMS_PARTIAL),
    ("write_trims", &WRITE_TRIMS), ("read_observed_growths", &READ_GROWTHS),
    ("write_observed_growths", &WRITE_GROWTHS), ("read_observed_growth_bytes", &READ_GROWTH_BYTES),
    ("write_observed_growth_bytes", &WRITE_GROWTH_BYTES), ("trim_observed_capacity_bytes", &TRIM_OBSERVED_CAPACITY),
    ("replacement_requested_capacity_bytes", &REPLACEMENT_CAPACITY)].into_iter().map(|(k,v)| (k,v.load(Ordering::Relaxed))).collect()
}
'''

def instrument(crate, patched):
    manifest = crate / "Cargo.toml"
    text = manifest.read_text().replace("[features]\n", "[features]\nbench-codec-diag = []\n", 1)
    manifest.write_text(text)
    lib = crate / "src/lib.rs"
    lib.write_text(lib.read_text() + '\n#[cfg(feature = "bench-codec-diag")]\n#[doc(hidden)]\npub mod codec_bench_metrics;\n')
    (crate / "src/codec_bench_metrics.rs").write_text(METRICS)
    parser = crate / "src/parser.rs"
    text = parser.read_text()
    text = text.replace('pub struct ValueCodec {', 'pub struct ValueCodec {\n        #[cfg(feature = "bench-codec-diag")]\n        diagnostic_read_capacity: usize,', 1)
    if patched:
        text = text.replace('state: AnySendSyncPartialState::default(),', 'state: AnySendSyncPartialState::default(),\n                #[cfg(feature = "bench-codec-diag")]\n                diagnostic_read_capacity: 0,', 1)
    needle = 'fn decode_stream(&mut self, bytes: &mut BytesMut, eof: bool) -> RedisResult<Option<Value>> {'
    assert text.count(needle) == 1
    text = text.replace(needle, needle + '\n            #[cfg(feature = "bench-codec-diag")]\n            crate::codec_bench_metrics::read(bytes.len(), bytes.capacity());\n            #[cfg(feature = "bench-codec-diag")]\n            crate::codec_bench_metrics::growth(true, self.diagnostic_read_capacity, bytes.capacity());')
    needle = 'dst.extend_from_slice(item.as_ref());'
    text = text.replace(needle, '#[cfg(feature = "bench-codec-diag")]\n            let diagnostic_before = dst.capacity();\n            ' + needle)
    assert text.count(needle) == 1
    text = text.replace(needle, needle + '\n            #[cfg(feature = "bench-codec-diag")]\n            crate::codec_bench_metrics::write(diagnostic_before, dst.capacity());')
    if patched:
        needle = '*bytes = BytesMut::with_capacity(REPLACEMENT_CODEC_BUFFER);'
        assert text.count(needle) == 1
        text = text.replace(needle, '#[cfg(feature = "bench-codec-diag")]\n                crate::codec_bench_metrics::read_trim(opt.is_some());\n                #[cfg(feature = "bench-codec-diag")]\n                crate::codec_bench_metrics::replacement(bytes.capacity(), REPLACEMENT_CODEC_BUFFER);\n                ' + needle)
        needle = '*dst = BytesMut::with_capacity(REPLACEMENT_CODEC_BUFFER);'
        assert text.count(needle) == 1
        text = text.replace(needle, '#[cfg(feature = "bench-codec-diag")]\n                    crate::codec_bench_metrics::write_trim();\n                    #[cfg(feature = "bench-codec-diag")]\n                    crate::codec_bench_metrics::replacement(dst.capacity(), REPLACEMENT_CODEC_BUFFER);\n                    ' + needle)
        mux = crate / 'src/aio/multiplexed_connection.rs'
        mux_text = mux.read_text()
        needle = '*framed.write_buffer_mut() ='
        assert mux_text.count(needle) == 1
        mux.write_text(mux_text.replace(needle, '#[cfg(feature = "bench-codec-diag")]\n            crate::codec_bench_metrics::write_trim();\n            #[cfg(feature = "bench-codec-diag")]\n            crate::codec_bench_metrics::replacement(framed.write_buffer().capacity(), crate::parser::REPLACEMENT_CODEC_BUFFER);\n            ' + needle))
    needle = '            match opt {'
    assert text.count(needle) == 1
    text = text.replace(needle, '#[cfg(feature = "bench-codec-diag")]\n            { self.diagnostic_read_capacity = bytes.capacity(); }\n' + needle)
    parser.write_text(text)

def main():
    os.environ['PATH'] = str(Path.home()/'.cargo/bin') + os.pathsep + os.environ.get('PATH','')
    p = argparse.ArgumentParser()
    p.add_argument('--diagnostics', action='store_true')
    p.add_argument('--jemalloc', action='store_true')
    args = p.parse_args()
    if args.diagnostics and args.jemalloc:
        p.error('jemalloc and system-allocation diagnostics are separate builds')
    cache = ROOT / 'source-cache'
    if not (cache / '.git').exists():
        subprocess.run(['git','clone','--no-checkout','https://github.com/ofirluzon/redis-rs.git',str(cache)],check=True)
    bin_dir = ROOT / 'bin'
    bin_dir.mkdir(exist_ok=True)
    for variant, commit in COMMITS.items():
        source = ROOT / 'vendor/upstream'
        shutil.rmtree(source, ignore_errors=True)
        source.mkdir(parents=True)
        archive = subprocess.Popen(['git','-C',str(cache),'archive',commit],stdout=subprocess.PIPE)
        with tarfile.open(fileobj=archive.stdout,mode='r|') as tar:
            # Trusted pinned public source archive, no shell execution or remote setup scripts.
            tar.extractall(source)
        if archive.wait() != 0:
            raise RuntimeError('git archive failed')
        instrument(source / 'redis',variant == 'patched')
        features = ['patched'] if variant == 'patched' else []
        if args.diagnostics: features += ['alloc-diagnostics','codec-diagnostics']
        if args.jemalloc: features += ['jemalloc']
        env = dict(os.environ, CODEC_BENCH_COMMIT=commit)
        command = ['cargo','build','--release']
        if (ROOT / 'Cargo.lock').exists(): command.append('--locked')
        if features: command += ['--features',','.join(features)]
        subprocess.run(command,cwd=ROOT,env=env,check=True)
        suffix = '-diagnostic' if args.diagnostics else '-jemalloc' if args.jemalloc else ''
        binary = bin_dir / (variant + suffix)
        shutil.copy2(ROOT / 'target/release/codec-bench',binary)
        metadata = {'commit':commit,'features':features,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                    'harness_sha256':hashlib.sha256((ROOT/'src/main.rs').read_bytes()).hexdigest(),
                    'lock_sha256':hashlib.sha256((ROOT/'Cargo.lock').read_bytes()).hexdigest(),
                    'rustc':subprocess.check_output(['rustc','-Vv'],text=True),
                    'instrumentation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'source_instrumentation':'cfg-gated; compiled out of scored builds'}
        binary.with_suffix('.build.json').write_text(json.dumps(metadata,indent=2))
    print('Builds prepared:', bin_dir)

if __name__ == '__main__': main()
