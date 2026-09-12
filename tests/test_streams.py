"""Independent clocks, immutable source streams, and global admission contracts."""
from copy import deepcopy
import json
import unittest

import test_calibration
from tracegen.calibration import calibrate, count_replay
from tracegen.generator import build_datasets, generate, validate_config
from tracegen.streams import rate_scales, source_segments


class IndependentStreams(unittest.TestCase):
    def setUp(self):
        test_calibration.NonemptyRequestMix.setUp(self)
        self.config.pop('session_rate')
        self.config.pop('arrival')
        for i, spec in enumerate(self.config['datasets']):
            spec.pop('weight')
            spec['traffic'] = dict(session_rate=.5+i*.2, arrival={'cv':1.5})

    def run_trace(self, config, name='out'):
        ds = build_datasets(config)
        prediction = count_replay(config, ds, rate_scales(config, ds))
        path = self.path / f'{name}.jsonl'
        manifest = generate(config, path, datasets=ds)
        self.assertEqual(prediction['request_counts'], {s['name']:s['requests'] for s in manifest['source_mix']})
        self.assertEqual(prediction['sessions'], [{k:s[k] for k in prediction['sessions'][0]}
                                                for s in manifest['sessions']] if prediction['sessions'] else [])
        self.assertEqual(prediction['peak_concurrent_sessions'], manifest['stats']['peak_concurrent_sessions'])
        self.assertEqual(prediction['pending_sessions_at_end'], manifest['stats']['pending_sessions_at_end'])
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([r['timestamp'] for r in rows], sorted(r['timestamp'] for r in rows))
        self.assertTrue(all(r['hash_ids'] for r in rows))
        return manifest, rows

    def test_source_trace_unchanged_when_other_sources_added_or_reordered(self):
        c = deepcopy(self.config);c.pop('max_concurrent_sessions')
        combined, rows = self.run_trace(c)
        c['datasets'].reverse()
        _, reordered = self.run_trace(c)
        self.assertEqual(rows, reordered)
        c['datasets'] = [c['datasets'][-1]]
        single, alone = self.run_trace(c)
        self.assertEqual(alone, [r for r in rows if r['session_id'].startswith('0:')])
        self.assertEqual(single['stats']['delayed_sessions'], 0)
        c['datasets'][0]['traffic']['session_rate'] = 8
        c['datasets'] += deepcopy(self.config['datasets'][1:])
        _, changed = self.run_trace(c)
        self.assertEqual([r for r in rows if r['session_id'].startswith('1:')],
                         [r for r in changed if r['session_id'].startswith('1:')])

    def test_shared_cap_burst_cutoff_and_simultaneous_offers(self):
        for cap, cv in [(1,0),(3,1.5),(100,5)]:
            c = deepcopy(self.config);c['max_concurrent_sessions']=cap
            for s in c['datasets']:
                s['traffic'] = dict(session_rate=1, arrival={'cv':cv},
                                    bursts=[dict(start=20,duration=10,session_rate=3)])
            manifest, _ = self.run_trace(c)
            self.assertLessEqual(manifest['stats']['peak_concurrent_sessions'],cap)
            if cap==1:
                self.assertGreater(manifest['stats']['delayed_sessions'],0)
                self.assertEqual(manifest['sessions'][0]['source'],'0')
                self.assertEqual(manifest['sessions'][1]['source'],'1')

    def test_calibration_preserves_candidate_budget_and_matches_request_targets(self):
        c=deepcopy(self.config);c.update(duration=1000,max_concurrent_sessions=100)
        ds=build_datasets(c)
        result=calibrate(c,ds,[.4,.4,.2],tolerance=.01,max_iterations=150)
        before=sum((s['end']-s['start'])*s['session_rate'] for spec in c['datasets'] for s in source_segments(c,spec))
        for spec,scale in zip(c['datasets'],result['weights']):spec['rate_scale']=scale
        after=sum((s['end']-s['start'])*s['session_rate'] for spec in c['datasets'] for s in source_segments(c,spec,spec['rate_scale']))
        self.assertAlmostEqual(before,after)
        m,_=self.run_trace(c)
        self.assertEqual(m['stats']['requests'],result['prediction']['requests'])
        for source,target in zip(m['source_mix'],[.4,.4,.2]):
            self.assertLessEqual(abs(source['requests']/m['stats']['requests']-target),.01)

    def test_zero_traffic_and_ambiguous_configs(self):
        c=deepcopy(self.config)
        for spec in c['datasets']:spec['traffic']['session_rate']=0
        m, rows=self.run_trace(c)
        self.assertEqual(rows,[])
        with self.assertRaisesRegex(ValueError,'zero-traffic'):
            calibrate(c,build_datasets(c),[1,1,1])
        for update in ({'session_rate':1}, {'max_concurrent_sessions':0}):
            c=deepcopy(self.config);c.update(update)
            with self.assertRaises(ValueError):validate_config(c)
        for key,value in [('traffic',None),('rate_scale',float('inf')),('weight',1)]:
            c=deepcopy(self.config);c['datasets'][0][key]=value
            with self.assertRaises(ValueError):validate_config(c)


if __name__=='__main__':
    unittest.main()
