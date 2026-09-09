from argparse import ArgumentParser, Namespace
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from prepare import prepare
from tracegen.frontends.common import ConversionContext, RawRecord
from tracegen.frontends.lmsys import LMSYSFrontend, HFChatTokenizer
from tracegen.frontends.swissai import SwissAIFrontend
from tracegen.frontends.weka import WekaFrontend
from tracegen.generator import build_datasets, generate
from tracegen.sources import compile_session
from tracegen.frontends.common import prefix_hashes
from tracegen.schema import validate_session


ROOT = Path(__file__).resolve().parents[1]


def options(frontend, *argv):
    parser = ArgumentParser()
    frontend.configure(parser)
    return parser.parse_args(argv)


def raw_rows(rows):
    return iter(RawRecord(row,0,i) for i,row in enumerate(rows))


def context():
    return ConversionContext([{'sha256':'test-input-fingerprint'}])


def swiss(t='2025-01-01T00:00:00Z', **extra):
    return dict(created_at=t, model='model-a', token_count=35, bucket_ids=[11,22,33], **extra)


class FakeTokenizer:
    metadata = {'name':'test-only-character-identities','tokenizer_sha256':'fixture',
                'chat_template_sha256':'fixture-template'}
    def __init__(self):
        self.calls = []
    def encode(self,messages):
        self.calls.append(deepcopy(messages))
        ids = []
        for message in messages:
            ids.append({'system':1000,'user':1001,'assistant':1002}[message['role']])
            ids.extend(map(ord,message['content']))
            ids.append(1003)
        return ids+[1002]


def conversation():
    return dict(conversation_id='chat-1',model='original-model',language='English',conversation=[
        {'role':'user','content':'Question one'}, {'role':'assistant','content':'Prior answer'},
        {'role':'user','content':'Question two'}, {'role':'assistant','content':'Current answer'}])


class FrontendSemantics(unittest.TestCase):
    def test_swiss_padding_sort_and_global_identity(self):
        frontend = SwissAIFrontend()
        opt = options(frontend,'--session-mode','window','--window-seconds','60','--block-size','32')
        ctx = context()
        rows = [swiss('2025-01-01T00:01:02Z'),swiss('2025-01-01T00:00:02Z'),swiss()]
        sessions = list(frontend.convert(raw_rows(rows),opt,ctx))
        self.assertEqual(len(sessions),2)
        self.assertEqual([r['timestamp'] for r in sessions[0]['requests']],[0,2])
        self.assertEqual(len(sessions[0]['requests'][0]['hash_ids']),1)
        self.assertEqual(ctx.stats['dropped_padding_buckets'],3)
        for s in sessions:
            validate_session(s)
            self.assertEqual(set(s),{'requests'})
            self.assertTrue(all(set(r)=={'timestamp','hash_ids'} for r in s['requests']))
        a,b = [compile_session(s,32,'swiss',i,scope='global') for i,s in enumerate(sessions)]
        self.assertEqual(len(a.requests[0].hashes),1)
        self.assertEqual(a.requests[0].hashes,b.requests[0].hashes)

    def test_swiss_model_namespaces_and_observed_sessions(self):
        frontend = SwissAIFrontend()
        opt = options(frontend,'--session-mode','field','--block-size','16')
        a,b = swiss(session_id='a'),swiss(session_id='a')
        b['model']='model-b'
        sessions=list(frontend.convert(raw_rows([a,b]),opt,context()))
        self.assertEqual(len(sessions),2)
        hashes=[compile_session(s,16,'swiss',i,scope='global').requests[0].hashes for i,s in enumerate(sessions)]
        self.assertTrue(set(hashes[0]).isdisjoint(hashes[1]))
        with self.assertRaisesRegex(ValueError,'session field'):
            list(frontend.convert(raw_rows([swiss()]),opt,context()))

    def test_swiss_rejects_missing_length_and_wrong_buckets(self):
        frontend=SwissAIFrontend()
        opt=options(frontend,'--session-mode','window')
        for update,remove in [({},'token_count'),({'token_count':100},None),
                              ({'bucket_ids':[True,2,3]},None)]:
            row=swiss();row.update(update)
            if remove:del row[remove]
            with self.subTest(row=row),self.assertRaises(ValueError):
                list(frontend.convert(raw_rows([row]),opt,context()))

    def test_lmsys_history_includes_previous_but_not_current_response(self):
        tokenizer=FakeTokenizer();frontend=LMSYSFrontend(tokenizer)
        opt=options(frontend,'--tokenizer','test','--turn-interval-mean','17','--turn-interval-cv','0','--block-size','4')
        raw=conversation()
        result=list(frontend.convert(raw_rows([raw]),opt,context()))[0]
        validate_session(result)
        self.assertEqual([r['timestamp'] for r in result['requests']],[0,17])
        self.assertEqual(tokenizer.calls[0],raw['conversation'][:1])
        self.assertEqual(tokenizer.calls[1],raw['conversation'][:3])
        self.assertEqual(set(result),{'requests'})
        a,b=[r['hash_ids'] for r in result['requests']]
        self.assertEqual(a,prefix_hashes(tokenizer.encode(raw['conversation'][:1]),4))
        self.assertGreater(len(a),0)
        self.assertEqual(a,b[:len(a)])

    def test_lmsys_timing_determinism_filter_and_invalid_dialogue(self):
        frontend=LMSYSFrontend(FakeTokenizer());opt=options(frontend,'--tokenizer','test')
        a=list(frontend.convert(raw_rows([conversation()]),opt,context()))
        b=list(frontend.convert(raw_rows([conversation()]),opt,context()))
        self.assertEqual(a,b)
        opt.seed+=1
        c=list(frontend.convert(raw_rows([conversation()]),opt,context()))
        self.assertNotEqual(a[0]['requests'][1]['timestamp'],c[0]['requests'][1]['timestamp'])
        bad=conversation();bad['conversation']=bad['conversation'][1:]
        with self.assertRaisesRegex(ValueError,'preceding user'):
            list(frontend.convert(raw_rows([bad]),opt,context()))
        opt.language='Chinese'
        ctx=context()
        self.assertEqual(list(frontend.convert(raw_rows([conversation()]),opt,ctx)),[])
        self.assertEqual(ctx.stats['filtered_records'],1)

    def test_backend_rejects_raw_datasets_and_ambiguous_content(self):
        with self.assertRaisesRegex(ValueError,'prepare.py'):
            compile_session({},4,'weka',0,'weka')
        for req in ({'timestamp':0,'token_ids':[1],'hash_ids':[2]},
                    {'timestamp':0,'requests':[]}, {'timestamp':False,'token_ids':[]}):
            with self.assertRaises(ValueError):
                validate_session({'requests':[req]})


class ConversionPipeline(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)

    def prepare_options(self,frontend,source,out,**extra):
        return Namespace(frontend=frontend,input=[source],output=out,limit=None,block_size=4,**extra)

    def test_weka_conversion_manifest_and_stale_detection(self):
        raw=ROOT/'examples/data/agent.jsonl';out=self.path/'weka.jsonl'
        m=prepare(WekaFrontend(),self.prepare_options('weka',raw,out))
        self.assertEqual(m['stats']['sessions'],2)
        config=dict(block_size=4,duration=10,max_concurrent_sessions=2,session_rate=1,
                    datasets=[dict(name='weka',path=str(out))])
        self.assertEqual(build_datasets(config)[0].normalization['frontend'],'weka')
        out.write_text(out.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'SHA256'):
            build_datasets(config)

    def test_invalid_input_and_lfs_do_not_replace_output(self):
        raw=self.path/'raw.jsonl';out=self.path/'out.jsonl';out.write_text('keep')
        for content in ('version https://git-lfs.github.com/spec/v1\noid sha256:dummy\n',
                        '{"requests": []}\n','invalid json\n'):
            raw.write_text(content)
            with self.assertRaises((ValueError,KeyError)):
                prepare(WekaFrontend(),self.prepare_options('weka',raw,out))
            self.assertEqual(out.read_text(),'keep')
        self.assertEqual(sorted(p.name for p in self.path.iterdir()),['out.jsonl','raw.jsonl'])

    def test_independent_sources_weighted_sampling_and_jitter_overrides(self):
        frontend=SwissAIFrontend();opt=options(frontend,'--session-mode','window','--window-seconds','1','--block-size','16')
        ss=list(frontend.convert(raw_rows([swiss(),swiss('2025-01-01T00:00:02Z')]),opt,context()))
        swiss_path=self.path/'swiss.jsonl';swiss_path.write_text(''.join(json.dumps(s)+'\n' for s in ss))
        lmsys=LMSYSFrontend(FakeTokenizer());opt=options(lmsys,'--tokenizer','test','--turn-interval-cv','0','--block-size','16')
        chat=list(lmsys.convert(raw_rows([conversation()]),opt,context()))
        chat_path=self.path/'chat.jsonl';chat_path.write_text(json.dumps(chat[0])+'\n')
        config=dict(block_size=16,duration=300,max_concurrent_sessions=10,session_rate=1,seed=42,
                    new_block_jitter=.3,datasets=[dict(name='swiss',path=str(swiss_path),weight=.5,new_block_jitter=0,hash_id_scope='global'),
                                                 dict(name='chat',path=str(chat_path),weight=.5)])
        output=self.path/'mix.jsonl';m=generate(config,output)
        self.assertEqual({s['name'] for s in m['source_mix']},{'swiss','chat'})
        self.assertTrue(all(s['requests']>0 for s in m['source_mix']))
        self.assertEqual(sum(s['block_references'] for s in m['source_mix']),m['stats']['blocks'])
        metadata={s['session_id']:s for s in m['sessions']}
        swiss_hashes=[]
        for row in map(json.loads,output.read_text().splitlines()):
            meta=metadata[row['session_id']]
            if meta['source']=='swiss':
                self.assertEqual(meta['new_block_jitter'],0)
                swiss_hashes.append(row['hash_ids'])
        self.assertGreater(len(swiss_hashes),1)
        self.assertTrue(all(h==swiss_hashes[0] for h in swiss_hashes))

    def test_minimal_files_need_no_sidecar_or_session_metadata(self):
        raw = {'requests': [{'timestamp': 0, 'hash_ids': ['a', 'b']},
                            {'timestamp': 2, 'hash_ids': ['a', 'c']}]}
        source = self.path/'minimal.jsonl'
        source.write_text(json.dumps(raw)+'\n')
        config = dict(block_size=128, duration=5, max_concurrent_sessions=10,
                      session_rate=1, arrival={'cv': 0}, new_block_jitter=0,
                      datasets=[dict(name='minimal', path=str(source))])
        dataset = build_datasets(config)[0]
        self.assertIsNone(dataset.normalization)
        a, b = dataset.get(0).requests
        self.assertEqual(len(a.hashes), 2)
        self.assertEqual(a.hashes[0], b.hashes[0])
        self.assertNotEqual(a.hashes[1], b.hashes[1])
        manifest = generate(config, self.path/'out.jsonl')
        self.assertGreater(manifest['stats']['requests'], 0)
        for field in ('id', 'schema_version', 'block_size', 'provenance', 'hash_namespace', 'hash_id_scope'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_session(dict(raw, **{field: 'obsolete'}))
        for field in ('num_tokens', 'token_ids', 'provenance'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_session({'requests': [dict(raw['requests'][0], **{field: 0})]})

    def test_prepared_block_size_is_checked_before_sampling(self):
        raw = ROOT/'examples/data/agent.jsonl'
        out = self.path/'prepared.jsonl'
        prepare(WekaFrontend(), self.prepare_options('weka', raw, out))
        config = dict(block_size=128, duration=5, max_concurrent_sessions=1,
                      session_rate=0, datasets=[dict(name='a', path=str(out))])
        with self.assertRaisesRegex(ValueError, 'prepared block_size must match'):
            build_datasets(config)

    def test_source_weights_do_not_depend_on_dataset_size(self):
        small, large = self.path/'small.jsonl', self.path/'large.jsonl'
        record = json.dumps({'requests': [{'timestamp': 0, 'hash_ids': [1]}]})+'\n'
        small.write_text(record)
        large.write_text(record*100)
        config = dict(block_size=128, duration=2001, max_concurrent_sessions=1,
                      session_rate=1, arrival={'cv': 0}, seed=42,
                      datasets=[dict(name='small', path=str(small), weight=3),
                                dict(name='large', path=str(large), weight=1)])
        datasets = build_datasets(config)
        self.assertEqual([len(d.offsets) for d in datasets], [1, 100])
        m = generate(config, self.path/'weighted.jsonl', datasets=datasets)
        self.assertAlmostEqual(m['source_mix'][0]['sessions']/m['stats']['sessions'], .75, delta=.035)
        config['datasets'][0]['weight'] = 1
        config['datasets'][1]['weight'] = 3
        n = generate(config, self.path/'reweighted.jsonl')
        self.assertAlmostEqual(n['source_mix'][0]['sessions']/n['stats']['sessions'], .25, delta=.035)
        self.assertEqual(small.read_text(), record)
        self.assertEqual(large.read_text(), record*100)

    def test_cli_external_plugin_and_plugin_help(self):
        plugin=self.path/'external_frontend.py'
        plugin.write_text('class Example:\n'
                          ' def configure(self, parser): parser.add_argument("--count",type=int,default=2)\n'
                          ' def convert(self, records, options, context):\n'
                          '  for row in records:\n'
                          '   yield {"requests":[{"timestamp":0,"hash_ids":[1]*options.count}]}\n')
        raw=self.path/'raw.jsonl';raw.write_text('{}\n');out=self.path/'out.jsonl'
        env=dict(os.environ,PYTHONPATH=str(self.path))
        result=subprocess.run([sys.executable,str(ROOT/'prepare.py'),'external_frontend:Example',
                               '--input',str(raw),'--output',str(out),'--count','9'],
                              env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(json.loads(out.read_text())['requests'][0]['hash_ids']),9)
        help_result=subprocess.run([sys.executable,str(ROOT/'prepare.py'),'swissai','--help'],
                                   capture_output=True,text=True)
        self.assertIn('--session-mode',help_result.stdout)

    @unittest.skipUnless(importlib.util.find_spec('pyarrow'), 'optional Parquet dependency absent')
    def test_parquet_lmsys_input(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        raw=self.path/'chat.parquet';pq.write_table(pa.Table.from_pylist([conversation()]),raw)
        frontend=LMSYSFrontend(FakeTokenizer());opt=options(frontend,'--tokenizer','test')
        opt=Namespace(**vars(opt),frontend='lmsys',input=[raw],output=self.path/'out.jsonl',limit=1)
        self.assertEqual(prepare(frontend,opt)['stats']['requests'],2)

    @unittest.skipUnless(importlib.util.find_spec('transformers'), 'optional tokenizer dependency absent')
    def test_real_tokenizer_chat_template_path(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import PreTrainedTokenizerFast
        backend=Tokenizer(models.WordLevel({'[UNK]':0,'user':1,'assistant':2,'hello':3},unk_token='[UNK]'))
        backend.pre_tokenizer=pre_tokenizers.Whitespace()
        tokenizer=PreTrainedTokenizerFast(tokenizer_object=backend,unk_token='[UNK]')
        tokenizer.chat_template="{% for m in messages %}{{ m.role }} {{ m.content }} {% endfor %}{% if add_generation_prompt %}assistant{% endif %}"
        tokenizer.save_pretrained(self.path/'tokenizer')
        actual=HFChatTokenizer(str(self.path/'tokenizer'))
        self.assertEqual(actual.encode([{'role':'user','content':'hello'}]),[1,3,2])
        self.assertEqual(len(actual.metadata['tokenizer_sha256']),64)


if __name__ == '__main__':
    unittest.main()
