from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tracegen.calibration import calibrate, count_replay
from tracegen.generator import build_datasets, generate


class NonemptyRequestMix(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.paths=[]
        records=[
            [{'requests':[{'timestamp':0,'hash_ids':[]}]},
             {'requests':[{'timestamp':0,'hash_ids':[]},{'timestamp':2,'hash_ids':[1]},
                          {'timestamp':3,'hash_ids':[]},{'timestamp':6,'hash_ids':[1,2]},
                          {'timestamp':100,'hash_ids':[]}]}],
            [{'requests':[{'timestamp':0,'hash_ids':[10]}]}],
            [{'requests':[{'timestamp':i,'hash_ids':[20]*(0 if i==2 else 1)} for i in range(10)]}]]
        for i,rows in enumerate(records):
            p=self.path/f'{i}.jsonl';p.write_text(''.join(json.dumps(r)+'\n' for r in rows));self.paths.append(p)
        self.config=dict(block_size=128,duration=80,max_concurrent_sessions=3,session_rate=2,
                         seed=42,new_block_jitter=.3,arrival={'cv':1.5},
                         datasets=[dict(name=str(i),path=str(p),weight=w) for i,(p,w) in enumerate(zip(self.paths,[.2,.5,.3]))])

    def test_filtering_precedes_sampling_and_preserves_retained_gaps(self):
        ds=build_datasets(self.config)[0]
        self.assertEqual(ds.record_numbers,[1])
        self.assertEqual(list(ds.timelines[0]),[0,4])
        self.assertEqual([r.offset for r in ds.get(0).requests],[0,4])
        self.assertEqual(ds.filter_stats,dict(input_sessions=2,input_requests=6,
                                            excluded_empty_sessions=1,excluded_empty_requests=4))
        m=generate(self.config,self.path/'out.jsonl')
        for r in map(json.loads,(self.path/'out.jsonl').read_text().splitlines()):
            self.assertTrue(r['hash_ids'])
        self.assertEqual(m['stats']['empty_hash_requests'],0)
        for s in m['sessions']:
            if s['source']=='0':
                self.assertEqual(s['source_record'],1)
                self.assertEqual(s['template_duration'],4)
        self.assertEqual(m['sources'][0]['filtering']['excluded_empty_requests'],4)

    def test_count_replay_matches_generation_with_cap_bursts_and_cutoff(self):
        for seed,cap,cv in [(0,1,0),(1,3,1.5),(42,100,1)]:
            c=deepcopy(self.config);c.update(seed=seed,max_concurrent_sessions=cap,arrival={'cv':cv},
                                          bursts=[dict(start=20,duration=5,session_rate=8)])
            ds=build_datasets(c);p=count_replay(c,ds,[d.weight for d in ds])
            m=generate(c,self.path/'out.jsonl',datasets=ds)
            self.assertEqual(p['request_counts'],{s['name']:s['requests'] for s in m['source_mix']})
            self.assertEqual(p['peak_concurrent_sessions'],m['stats']['peak_concurrent_sessions'])
            self.assertEqual(p['pending_sessions_at_end'],m['stats']['pending_sessions_at_end'])
            self.assertEqual(p['sessions'],[{k:s[k] for k in p['sessions'][0]} for s in m['sessions']])

    def test_final_request_ratio_calibrated_without_dropping_valid_requests(self):
        c=deepcopy(self.config);c.update(duration=1000,max_concurrent_sessions=100)
        ds=build_datasets(c);result=calibrate(c,ds,[.4,.4,.2],tolerance=.01)
        for d,s,w in zip(ds,c['datasets'],result['weights']):d.weight=w;s['weight']=w
        m=generate(c,self.path/'calibrated.jsonl',datasets=ds)
        self.assertEqual(m['stats']['requests'],result['prediction']['requests'])
        self.assertNotEqual(result['weights'],[.4,.4,.2])
        for source,target in zip(m['source_mix'],[.4,.4,.2]):
            self.assertLessEqual(abs(source['requests']/m['stats']['requests']-target),.01)
        self.assertEqual(m['stats']['requests']+m['stats']['omitted_requests_at_end'],
                         sum(s['template_requests'] for s in m['sessions']))

    def test_all_empty_source_and_unreachable_request_mix_fail_explicitly(self):
        self.paths[0].write_text('{"requests":[{"timestamp":0,"hash_ids":[]}]}\n')
        output=self.path/'keep.jsonl';output.write_text('keep')
        with self.assertRaisesRegex(ValueError,'no nonempty requests'):
            generate(self.config,output)
        self.assertEqual(output.read_text(),'keep')
        c=deepcopy(self.config);c['datasets']=c['datasets'][1:];c.update(duration=.000001,session_rate=1)
        with self.assertRaisesRegex(ValueError,'no nonempty requests arrive'):
            calibrate(c,build_datasets(c),[.5,.5])


if __name__=='__main__':
    unittest.main()
