"""工作台与 CLI 同源、导出、统计筛选及失败恢复。"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_synthesis import config
from tracegen.analysis import analyze
from tracegen.synthesis import generate
from tracegen.workbench import Workbench, make_server, distribution_preview


class WorkbenchTests(unittest.TestCase):
    def test_replay_filter_and_baseline_are_immutable(self):
        c=config()
        c['tasks'][0]['clients']={'count':2}
        other=copy.deepcopy(c['tasks'][0]);other['key']='agent';other['weight']=2
        c['tasks'].append(other)
        with tempfile.TemporaryDirectory() as tmp:
            backend=Workbench(Path(tmp)/'web')
            provided=copy.deepcopy(c)
            original=backend.run(provided)
            provided['seed']=999
            self.assertEqual(original['config']['seed'], c['seed'])
            trace=backend.artifact(original['run_id'],'trace.jsonl')
            data=trace.read_bytes()
            generate(c,Path(tmp)/'cli.jsonl')
            self.assertEqual(data,(Path(tmp)/'cli.jsonl').read_bytes())
            selected=backend.report(original['run_id'],1,'chat','client-000000')
            self.assertEqual(trace.read_bytes(),data)
            meta=backend.runs[original['run_id']]
            wanted={s['session_id'] for s in meta['sessions'] if s['task']=='chat' and s['client']=='client-000000'}
            rows=[json.loads(line) for line in data.splitlines()]
            self.assertEqual(selected['report']['stats']['requests'],sum(r['session_id'] in wanted for r in rows))
            self.assertEqual(selected['report']['stats']['sessions'],len(wanted))
            empty=backend.report(original['run_id'],1,'missing')['report']
            self.assertEqual(empty['stats']['requests'],0)
            self.assertEqual(empty['concurrency'],[])
            changed=copy.deepcopy(c);changed['tasks'][0]['session']['gap']=5
            second=backend.run(changed)
            self.assertNotEqual(original['run_id'],second['run_id'])
            self.assertEqual(backend.report(original['run_id'])['sha256'],original['sha256'])
            self.assertEqual(trace.read_bytes(),data)
            self.assertEqual(json.loads(backend.artifact(original['run_id'],'config.json').read_text()),c)

    def test_distribution_preview_does_not_consume_generation_rng(self):
        spec={'distribution':'gamma','mean':5,'cv':1,'min':1,'max':12}
        a=distribution_preview(spec,'requests')
        self.assertEqual(a,distribution_preview(spec,'requests'))
        self.assertAlmostEqual(sum(p[2] for p in a['histogram']),1)
        self.assertTrue(all(1<=p[0]<=12 for p in a['cdf']))
        fixed=distribution_preview(3,'requests')
        self.assertEqual(fixed['histogram'],[[3,4,1]])
        density=distribution_preview(spec,'gap')
        self.assertFalse(density['discrete'])
        self.assertAlmostEqual(sum((b-a)*p for a,b,p in density['density']),1)

    def test_http_workflow_errors_and_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            static=Path(tmp)/'static';static.mkdir();(static/'index.html').write_text('workbench')
            server=make_server(port=0,output=Path(tmp)/'runs',static=static)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            root=f'http://127.0.0.1:{server.server_port}'
            def post(route,data):
                request=Request(root+'/api/'+route,json.dumps(data).encode(),{'Content-Type':'application/json'})
                try:
                    with urlopen(request) as response:return json.load(response)
                except HTTPError as exc:
                    exc.close()
                    raise
            def get(path):
                try:
                    with urlopen(root+path) as response:return response.read()
                except HTTPError as exc:
                    exc.close()
                    raise
            try:
                with urlopen(root) as response:self.assertEqual(response.read(),b'workbench')
                with urlopen(root+'/api/presets') as response:
                    presets=json.load(response)
                expected=1+len(list((Path(__file__).resolve().parents[1]/'examples/presets').glob('*.json')))
                self.assertEqual(len(presets),expected)
                with self.assertRaises(HTTPError) as error:post('run',{'config':{'version':2}})
                self.assertEqual(error.exception.code,400)
                result=post('run',{'config':config()})
                run_id=result['run_id']
                exported=get(f'/api/files/{run_id}/trace.jsonl')
                self.assertTrue(exported)
                report=post('analyze',{'run_id':run_id,'window':1})
                self.assertEqual(result['sha256'],report['sha256'])
                with self.assertRaises(HTTPError):post('analyze',{'run_id':run_id,'window':0})
                with self.assertRaises(HTTPError):get('/api/files/'+'f'*64+'/trace.jsonl')
                with self.assertRaises(HTTPError):get('/../generate.py')
                self.assertEqual(post('distribution',{'spec':2,'field':'requests'})['mean'],2)
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
