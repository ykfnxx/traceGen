import json
from pathlib import Path
import tempfile
import unittest

from experiments.validate_paper_patterns import describe, ks, measure


class PaperStatisticsTests(unittest.TestCase):
    def test_ks_with_ties_and_cv(self):
        self.assertAlmostEqual(ks([0, 0, 2], [0, 1, 1]), 1/3)
        self.assertEqual(describe([0, 2])['cv'], 1)

    def test_counts_include_idle_seconds_and_iat_excludes_window_crossing(self):
        rows = [dict(timestamp=t, session_id='s', input_tokens=256,
                     output_tokens=64, external_tokens=32, hash_ids=[])
                for t in [.1, .2, 300.1]]
        manifest = dict(config=dict(duration=3600), clients=[{}],
                        sessions=[dict(session_id='s', task='t', client='c',
                                       emitted_requests=3, planned_requests=3)],
                        stats=dict(truncated_sessions=0), output_sha256_uncompressed='fixture')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trace.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            report=measure(path, manifest)
        self.assertEqual(report['iat_seconds']['n'], 2)
        self.assertEqual(report['windows_300s'][0]['iat_cv'], 0)
        self.assertIsNone(report['windows_300s'][1]['iat_cv'])
        self.assertAlmostEqual(report['windows_300s'][0]['count_cv'], 299**.5)
        self.assertAlmostEqual(report['windows_300s'][0]['mssd'], 4/299)
        self.assertAlmostEqual(report['adjacent_windows'][0]['ks'], 1/300)
        self.assertAlmostEqual(report['adjacent_windows'][0]['w1'], 1/300)
        self.assertAlmostEqual(report['millisecond_fano'], 1-3/3600000)
        self.assertEqual(report['hourly_top5pct_share'][0], 1)
        self.assertEqual(report['identified_multiturn_request_share'], 1)
