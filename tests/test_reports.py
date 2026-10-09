import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze import DIMENSIONS

def rows():
    base={d:0 for d in DIMENSIONS}
    base.update(variant='baseline',source_commit='fixture',binary_sha256='fixture',
                workload='small',connections=1,inflight_limit=128,pipeline_batch=1,
                target_commands_s=50000,duration_s=300,warmup_s=30,idle_s=30,workers=4,
                spike_mode='random',group_scope='aggregate',group='all',payload_bytes=None,
                diagnostic=False,allocator='system',status='OK',group_commands=100,
                total_commands=100,total_cpu_s=.001,steady_rss_mib=10,sampled_peak_rss_mib=12,
                achieved_commands_s=50000,response_mean_us=20,response_p50_us=15,
                response_p99_us=40,response_p999_us=None,errors=0)
    return [dict(base,id=f'small-r{i+1}-baseline',cpu_us_per_command=value,seed=42+i) for i,value in enumerate([10,10,40])]

class ReportTests(unittest.TestCase):
    def test_mean_is_not_median_and_repetitions_are_retained(self):
        from analyze import mean_rows,median_rows
        self.assertEqual(mean_rows(rows())[0]['cpu_us_per_command'],20)
        self.assertEqual(median_rows(rows())[0]['cpu_us_per_command'],10)
        self.assertEqual(mean_rows(rows())[0]['repetitions'],3)

    def test_human_report_has_all_runs_mean_and_missing_percentile(self):
        from reporting import markdown_report
        text=markdown_report(rows(),3,3)
        for expected in ['r1: 10.00','r2: 10.00','r3: 40.00','Mean: 20.00','p99.9','INCOMPLETE']:
            if expected=='INCOMPLETE': self.assertNotIn(expected,text)
            else: self.assertIn(expected,text)
        self.assertIn('—',text)
        self.assertIn('not pooled percentiles',text)

    def test_partial_report_does_not_claim_three_results(self):
        from reporting import markdown_report
        text=markdown_report(rows()[:2],2,3)
        self.assertIn('INCOMPLETE',text)
        self.assertIn('2/3',text)

    def test_missing_repetition_and_missing_metric_are_visible(self):
        from reporting import markdown_report
        subset=[rows()[0],dict(rows()[2],response_p999_us=100)]
        text=markdown_report(subset,2,3)
        self.assertIn('r2: missing run',text)
        self.assertIn('Mean (n=1): 100.00',text)

    def test_plan_reveals_unfinished_versions_and_last_repetition(self):
        from reporting import markdown_report
        plan=[]
        for variant in ['baseline','patched-off']:
            for r in rows():
                plan.append(dict(id=r['id'].replace('baseline',variant),variant=variant,
                    workload=r['workload'],connections=1,per_connection_inflight=128,pipeline_batch=1,
                    rate=50000,duration_s=300,warmup_s=30,idle_s=30,workers=4,spike_mode='random'))
        text=markdown_report(rows()[:2],2,6,plan=plan)
        self.assertIn('baseline (2/3)',text)
        self.assertIn('patched-off (0/3)',text)
        self.assertIn('r3: missing run',text)
        self.assertIn('Mean (n=2): 10.00',text)

    def test_complete_run_without_spikes_is_not_a_missing_run(self):
        from reporting import markdown_report
        subset=[dict(r,workload='rare') for r in rows()]
        subset.append(dict(subset[0],group='spike',group_commands=1))
        text=markdown_report(subset,3,3)
        self.assertIn('r2: no observations',text)
        self.assertNotIn('missing run',text)

    def test_mean_reports_metric_contributors_and_limited_runs(self):
        from analyze import mean_rows
        subset=rows()
        subset[0]['response_p999_us']=100
        subset[1]['status']='LIMITED'
        result=mean_rows(subset)[0]
        self.assertEqual(result['response_p999_us_runs'],1)
        self.assertEqual(result['response_p999_us'],100)
        self.assertEqual(result['limited_runs'],1)
        self.assertNotIn('seed',result)

    def test_configuration_changes_remain_separate(self):
        from analyze import mean_rows
        self.assertEqual(len(mean_rows(rows()+[dict(rows()[0],workers=1)])),2)

    def test_all_comparison_stages_use_three_repetitions(self):
        import json
        from make_plans import generate
        matrix=json.loads((Path(__file__).resolve().parents[1]/'matrix.json').read_text())
        for stage,cfg in matrix['stages'].items():
            self.assertEqual(cfg['repetitions'],1 if stage=='small-capacity-pilot' else 3)
            plans,_=generate(matrix,stage)
            for c in plans:
                peers=[r for r in plans if all(r[k]==c[k] for k in ['variant','workload','rate','connections','per_connection_inflight','pipeline_batch'])]
                self.assertEqual(len(peers),cfg['repetitions'])

if __name__=='__main__':unittest.main()
