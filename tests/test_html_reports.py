"""Portable exporter and frozen-dataset guards; no private results in fixtures."""
import ast
import csv
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPORTS = Path(__file__).resolve().parents[1] / 'reports' / 'arm_20261009'
sys.path.insert(0, str(REPORTS))
from report_data import validate_report_data, validate_saved_rows


class HtmlReportTests(unittest.TestCase):
    def test_metric_fingerprint_ignores_column_order_but_rejects_changed_values(self):
        rows = [{'id': 'fixture', 'cpu': '12', 'rss': '64'}]
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        manifest = {'main': {'per_run.csv': digest}}
        with patch('report_data.pathlib.Path.read_text', return_value=json.dumps(manifest)):
            validate_saved_rows('main', 'per_run.csv', [{'rss': '64', 'cpu': '12', 'id': 'fixture'}])
            with self.assertRaisesRegex(ValueError, 'frozen October'):
                validate_saved_rows('main', 'per_run.csv', [{'id': 'fixture', 'cpu': '12', 'rss': '65'}])

    def test_share_export_rejects_stale_embedded_metrics_and_missing_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = {'rows': [], 'means': [], 'medians': []}
            for stage in ['main', 'knee-validation']:
                parent = root / 'results' / stage
                parent.mkdir(parents=True)
                for kind, filename in [('rows', 'per_run.csv'), ('means', 'means.csv'), ('medians', 'medians.csv')]:
                    with (parent / filename).open('w', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=['cpu', 'rss'])
                        writer.writeheader()
                        writer.writerow({'cpu': '12', 'rss': '64'})
                    data[kind].append({'cpu': '12', 'rss': '64', 'stage': stage, 'derived_metric': 1.2})
            with patch('report_data.validate_saved_rows'):
                validate_report_data(data, root)
                data['rows'][0]['cpu'] = '13'
                with self.assertRaisesRegex(ValueError, 'differs from validated observations'):
                    validate_report_data(data, root)
                data['rows'][0]['cpu'] = '12'
                data['medians'].pop()
                with self.assertRaisesRegex(ValueError, 'both October measurement groups'):
                    validate_report_data(data, root)

    @unittest.skipUnless(shutil.which('node'), 'Node is optional for JavaScript export checks')
    def test_csv_export_preserves_quotes_newlines_missing_values_and_unicode(self):
        tree = ast.parse((REPORTS / 'build_share_report.py').read_text())
        script = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'download_js' for t in n.targets))
        script = script.removeprefix('<script>').removesuffix('</script>')
        rows = [{'label': 'a,"b"\r\nc', 'cpu': 12.5, 'missing': None}, {'label': 'µs', 'extra': 'tail'}]
        program = ('const D=' + json.dumps({'rows': rows}) + ';const document={querySelectorAll:()=>[]};\n'
                   + script + '\nlet output;saveEmbeddedFile=(name,content,type)=>{output={name,content,type}};'
                   + 'exportEmbeddedCsv("rows");process.stdout.write(JSON.stringify(output));')
        result = json.loads(subprocess.check_output([shutil.which('node'), '-e', program], text=True))
        self.assertEqual(result['name'], 'redis-rs-codec-rows.csv')
        self.assertTrue(result['content'].startswith('\ufeff'))
        parsed = list(csv.DictReader(io.StringIO(result['content'].lstrip('\ufeff'))))
        self.assertEqual(parsed[0]['label'], rows[0]['label'])
        self.assertEqual(parsed[0]['cpu'], '12.5')
        self.assertEqual(parsed[0]['missing'], '')
        self.assertEqual(parsed[1]['extra'], 'tail')
        self.assertEqual(parsed[1]['label'], 'µs')


if __name__ == '__main__':
    unittest.main()
