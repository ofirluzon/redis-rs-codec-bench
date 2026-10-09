import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

class PlanValidation(unittest.TestCase):
    def test_rejects_paths_and_unknown_fields_before_launch(self):
        from validation import normalize_plan
        case = dict(id='ok', variant='baseline', workload='small', connections=1,
                    per_connection_inflight=4, rate=50, duration_s=2)
        self.assertEqual(normalize_plan([case])[0]['workers'], 4)
        for change in [dict(id='../outside'), dict(ratee=50), dict(rate=True),
                       dict(pipeline_batch=5), dict(workload='single-spike', duration_s=5),
                       dict(workload='single-spike', warmup_s=0, duration_s=7, pipeline_batch=2)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_plan([dict(case, **change)])

    def test_duplicate_ids_rejected(self):
        from validation import normalize_plan
        case = dict(id='same', variant='baseline', workload='small', connections=1,
                    per_connection_inflight=1, rate=50, duration_s=2)
        with self.assertRaises(ValueError): normalize_plan([case, case])


class MatrixReview(unittest.TestCase):
    def setUp(self):
        import json
        self.matrix=json.loads((Path(__file__).resolve().parents[1]/'matrix.json').read_text())

    def test_pipeline_controls_have_identical_budget(self):
        from make_plans import generate
        rows,_=generate(self.matrix,'pipeline-screening')
        self.assertEqual(len(rows),144)
        for r in rows:
            self.assertEqual(r['connections']*r['per_connection_inflight'],64)
            control=[c for c in rows if all(c[k]==r[k] for k in ['connections','rate','workload','variant','seed']) and c['pipeline_batch']==1]
            self.assertEqual(len(control),1)

    def test_primary_matrix_and_disabled_control(self):
        from make_plans import generate
        rows,_=generate(self.matrix,'main')
        self.assertEqual(len(rows),135)
        self.assertEqual({r['connections'] for r in rows},{1,16,100})
        self.assertEqual({r['variant'] for r in rows},{'baseline','patched-off','patched-on'})
        self.assertEqual(sum(r['workload']=='large' and r['rate']==200 for r in rows),27)
        self.assertTrue(all(r['duration_s']==300 for r in rows))

    def test_cli_overrides_generate_valid_frozen_cases(self):
        from make_plans import generate
        rows,_=generate(self.matrix,'screening',dict(connections=[2,8],profiles=['rare'],repetitions=3,duration_s=180,workers=1))
        self.assertEqual(len(rows),18)
        self.assertTrue(all(r['workers']==1 and r['duration_s']==180 for r in rows))

class ResultReview(unittest.TestCase):
    def test_changed_artifact_cannot_be_resumed(self):
        import json
        import tempfile
        from validation import checksums,load_done
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp); attempt=parent/'attempt-001';attempt.mkdir()
            (attempt/'summary.json').write_text('{}')
            (parent/'DONE.json').write_text(json.dumps(dict(attempt='attempt-001',build_sha256='hash',files_sha256=checksums(attempt))))
            (attempt/'summary.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'artifacts changed'):
                load_done(parent,{},dict(binary_sha256='hash'),False)

    def test_different_durations_workers_modes_and_builds_never_merge(self):
        from analyze import DIMENSIONS,median_rows
        base={d:0 for d in DIMENSIONS};base.update(status='OK',cpu_us_per_command=10)
        rows=[base,dict(base,cpu_us_per_command=20)]
        for key in ['duration_s','workers','spike_mode','binary_sha256']:
            rows.append(dict(base,**{key:1}))
        result=median_rows(rows)
        self.assertEqual(len(result),5)
        merged=next(r for r in result if r['repetitions']==2)
        self.assertEqual(merged['cpu_us_per_command'],15)

    def test_idle_rss_and_growth_use_separate_windows(self):
        from analyze import rss_metrics
        samples=[dict(phase=1,elapsed_s=t,rss_bytes=t*2**20,inflight_commands=1,inflight_payload_bytes=1024) for t in [0,30,60,90,119]]
        samples+= [dict(phase=2,elapsed_s=130,rss_bytes=10*2**20),dict(phase=3,elapsed_s=132,rss_bytes=5*2**20)]
        result=rss_metrics(samples,dict(config=dict(duration_s=120),measure_start_s=0,process_peak_rss_bytes_includes_warmup=200*2**20))
        self.assertEqual(result['sampled_peak_rss_mib'],119)
        self.assertAlmostEqual(result['rss_slope_mib_per_min'],60)
        self.assertEqual(result['idle_final_rss_mib'],10)
        self.assertEqual(result['after_drop_rss_mib'],5)

    def test_host_counters_exclude_loopback_from_nic_claims(self):
        from analyze import parse_counters
        row=dict(proc_stat='cpu 10 0 5 100 5 0 0 0 10 0\n',net_dev='headers\nheaders\n lo: 10 1 0 0 0 0 0 0 20 1 0 0 0 0 0 0\n eth0: 30 1 0 0 0 0 0 0 40 1 0 0 0 0 0 0',net_snmp='Tcp: RetransSegs InErrs OutRsts\nTcp: 3 0 1')
        result=parse_counters(row)
        self.assertEqual(result['host_cpu_total_ticks'],120)
        self.assertEqual(result['net_eth0_rx_bytes'],30)
        self.assertEqual(result['tcp_RetransSegs'],3)

class RunnerLockReview(unittest.TestCase):
    def test_parallel_matrix_is_rejected_before_contacting_endpoint(self):
        import fcntl, json, os, subprocess, tempfile
        lock_path=Path(tempfile.gettempdir())/f'redis-rs-codec-bench-{os.getuid()}.lock'
        with lock_path.open('a') as lock, tempfile.TemporaryDirectory() as temp:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: self.skipTest('Another matrix is running')
            plan=Path(temp)/'plan.json'
            plan.write_text(json.dumps([dict(id='locked',variant='baseline',workload='small',connections=1,
                                            per_connection_inflight=1,rate=50,duration_s=2)]))
            runner=Path(__file__).resolve().parents[1]/'scripts/run_matrix.py'
            result=subprocess.run([sys.executable,str(runner),str(plan),'--results',str(Path(temp)/'results'),'--allow-non-linux'],
                                  env=dict(os.environ,REDIS_URL='redis://127.0.0.1:1'),capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertIn('Another benchmark matrix is running',result.stderr)

if __name__ == "__main__": unittest.main()
