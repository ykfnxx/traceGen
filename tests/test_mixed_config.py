"""The mixed entrypoint discovers all sources from configured file paths."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.run_mixed import main, run_config


class MixedConfig(unittest.TestCase):
    def test_arbitrary_file_list_and_names_with_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for count in (1, 4):
                with self.subTest(sources=count):
                    specs = []
                    for i in range(count):
                        path = root / f'custom-{i}.jsonl'
                        path.write_text(json.dumps({'requests':[
                            {'timestamp':0, 'hash_ids':[i+1]}]})+'\n')
                        specs.append(dict(name=f'custom-task-{i}', path=path.name,
                                          traffic=dict(session_rate=1, arrival={'cv':0})))
                    config = root / 'mix.json'
                    config.write_text(json.dumps(dict(block_size=128, duration=5,
                                                      seed=7, datasets=specs)))
                    output = root / f'output-{count}'
                    # Exercise actual reading, synthesis, schedule checks and analysis;
                    # only rendering is skipped so this test needs no matplotlib.
                    with patch('experiments.run_mixed.plot'), contextlib.redirect_stdout(io.StringIO()):
                        run_config(config, output)
                    report = json.loads((output/'report.json').read_text())
                    mix = report['runs'][0]['source_mix']
                    self.assertEqual([s['name'] for s in mix], [s['name'] for s in specs])
                    self.assertEqual([s['requests'] for s in mix], [4]*count)
                    self.assertEqual(report['runs'][0]['checks'], 'passed')
                    self.assertEqual([s['path'] for s in report['config']['datasets']],
                                     [str(root/s['path']) for s in specs])

    def test_config_is_required_no_default_datasets(self):
        with patch('sys.argv', ['run_mixed.py']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                main()
        self.assertEqual(exc.exception.code, 2)
