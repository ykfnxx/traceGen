import json
from pathlib import Path
import tempfile
import unittest
from tracegen.synthesis import generate
from tracegen.analysis import analyze
from tracegen.clientpool import ClientPool
from tracegen.tracelab import TraceLab
from experiments.run_synthetic import run_config


def row(i, t, tokens, users):
    return dict(provider='claude', session_id='source', round_id=str(i), round_index=i,
        input_tokens_total=tokens, output_tokens=3, current_user_message_count=users,
        timing_events=[dict(event_type='user_message' if users else 'tool_result',
                            timestamp=f'2026-01-01T00:00:{t:02d}+00:00'),
                       dict(event_type='text', timestamp=f'2026-01-01T00:00:{t+1:02d}+00:00')])


class Sampling(unittest.TestCase):
    def test_sample_structure_hashes_labels_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);source=p/'source.jsonl'
            source.write_text('\n'.join(json.dumps(r) for r in [row(0,0,16,1),row(1,2,24,0),row(2,4,8,1),row(3,6,24,0)]))
            cfg=dict(version=3,duration=8,seed=42,block_size=4,
                traffic=dict(session_rate=1,arrival={'cv':0}),
                tasks=[dict(key='coding',source={'type':'tracelab','path':'source.jsonl'},session={'max_context_tokens':32})])
            (p/'config.json').write_text(json.dumps(cfg))
            a=run_config(p/'config.json',p/'out',plots=False)
            m=json.loads((p/'out/trace.jsonl.manifest.json').read_text())
            rows=[json.loads(l) for l in (p/'out/trace.jsonl').read_text().splitlines()]
            sid=rows[0]['session_id'];seq=[r for r in rows if r['session_id']==sid]
            self.assertEqual([r['timestamp'] for r in seq],[1,3,5,7])
            self.assertEqual([r['input_tokens'] for r in seq],[16,24,8,24])
            self.assertEqual([r['is_agent_turn_end'] for r in seq],[False,True,False,None])
            self.assertEqual(seq[0]['hash_ids'],seq[1]['hash_ids'][:4])
            self.assertTrue(set(seq[1]['hash_ids']).isdisjoint(seq[2]['hash_ids']))
            self.assertTrue(set(seq[1]['hash_ids']).isdisjoint(seq[3]['hash_ids']))
            self.assertEqual(seq[2]['hash_ids'],seq[3]['hash_ids'][:2])
            other=next(r for r in rows if r['session_id']!=sid)
            self.assertTrue(set(seq[0]['hash_ids']).isdisjoint(other['hash_ids']))
            self.assertTrue(all(s['task']=='coding' and s['source_session_id']=='source' for s in m['sessions']))
            self.assertEqual(sum(s['emitted_requests'] for s in m['sessions']),len(rows))
            self.assertEqual(m['sources'][0]['used_source_sessions'],1)
            self.assertEqual(a['cdfs'].get('external_tokens',[]),[])
            self.assertIn('user_boundary_gap',a['cdfs'])
            run_config(p/'config.json',p/'repeat',plots=False)
            self.assertEqual((p/'out/trace.jsonl').read_bytes(),(p/'repeat/trace.jsonl').read_bytes())
            cfg['duration']=4;cfg['output']={'request_metadata':False}
            manifest=generate(cfg,p/'short',p)
            short=[json.loads(l) for l in (p/'short').read_text().splitlines()]
            self.assertEqual([r['is_agent_turn_end'] for r in short if r['session_id']==sid],[False,True])
            for r in short:self.assertIn('current_user_message_count',r)
            del short[0]['is_agent_turn_end']
            (p/'old').write_text('\n'.join(json.dumps(r) for r in short))
            with self.assertRaises(KeyError):analyze(p/'old',manifest)

    def test_screen_and_independent_client_clock(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);rows=[row(0,1,10,1),row(1,1,11,0)]
            for sid,changes in [('badtime',{'timing_events':[]}),('badcount',{'current_user_message_count':2}),('big',{'input_tokens_total':100})]:
                rows.append(dict(row(0,1,10,1),session_id=sid,**changes))
            path=p/'rows';path.write_text('\n'.join(map(json.dumps,rows)))
            source=TraceLab(path);templates,info=source.select(20)
            self.assertEqual(len(templates),1);self.assertEqual(info['raw_sessions'],4)
            self.assertEqual(templates[0]['records'][1]['relative_time'],0)
            with self.assertRaises(ValueError):source.select(1)
            cfg=dict(version=3,duration=10,seed=9,block_size=4,traffic={'session_rate':1},tasks=[dict(key='a',source={'type':'tracelab','path':str(path)},session={'max_context_tokens':20})])
            pool=ClientPool(cfg);a=list(pool.clients[0].offers(9))
            cfg['tasks'][0]['session']['max_context_tokens']=200
            b=list(ClientPool(cfg).clients[0].offers(9));self.assertEqual(a,b)
            cfg['tasks'][0]['session']['gap']=1
            with self.assertRaises(ValueError):ClientPool(cfg)

    def test_mixed_tasks_backward_time_and_singleton(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            rows=[row(0,5,8,1),row(1,1,12,0),dict(row(0,0,0,1),session_id='single')]
            source=p/'source';source.write_text('\n'.join(map(json.dumps,rows)))
            templates,info=TraceLab(source).select(None)
            self.assertEqual(info['excluded_session_reasons'],{'backward_anchor':1})
            self.assertEqual(templates[0]['records'][0]['is_agent_turn_end'],None)
            cfg=dict(version=3,duration=5,seed=0,block_size=4,
                traffic={'session_rate':2,'arrival':{'cv':0}},tasks=[
                    dict(key='coding',source={'type':'tracelab','path':str(source)}),
                    dict(key='chat',session={'requests':2,'initial_private_tokens':8,'gap':1})])
            m=generate(cfg,p/'trace');r=analyze(p/'trace',m,window=1)
            lookup={s['session_id']:s for s in m['sessions']}
            counts={'chat':0,'coding':0}
            for line in (p/'trace').read_text().splitlines():
                record=json.loads(line);task=lookup[record['session_id']]['task'];counts[task]+=1
                if task=='chat':
                    self.assertIsNone(record['current_user_message_count'])
                    self.assertIsNone(record['is_agent_turn_end'])
                else:self.assertEqual(record['hash_ids'],[])
            self.assertEqual(counts,{t['key']:t['requests'] for t in m['task_mix']})
            self.assertIn('unknown_gap',r['cdfs'])
