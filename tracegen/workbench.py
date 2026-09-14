"""本地交互工作台的 HTTP 接口，复用 CLI 生成和分析核心。"""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import random
import re
import threading
from urllib.parse import urlparse

from .analysis import analyze, cdf
from .clientpool import ClientPool
from .validation import positive
from .profiles import Distribution
from .synthesis import generate

ROOT = Path(__file__).resolve().parents[1]


class Workbench:
    def __init__(self, output):
        self.output = Path(output)
        self.runs = {}
        self.lock = threading.Lock()

    def run(self, config, window=10, task=None, client=None):
        positive(window, 'window')
        encoded = json.dumps(config, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        config = json.loads(encoded)
        run_id = hashlib.sha256(encoded).hexdigest()
        with self.lock:
            if run_id not in self.runs:
                directory = self.output / run_id
                manifest = generate(config, directory / 'trace.jsonl')
                (directory / 'config.json').write_bytes(encoded)
                self.runs[run_id] = manifest
        return self.report(run_id, window, task, client)

    def report(self, run_id, window=10, task=None, client=None):
        if run_id not in self.runs:
            raise ValueError('未知运行，请重新生成预览')
        manifest = self.runs[run_id]
        report = analyze(self.output / run_id / 'trace.jsonl', manifest, window, task, client)
        return dict(run_id=run_id, sha256=manifest['output_sha256_uncompressed'], config=manifest['config'],
                    clients=[dict(task=c['task'], key=c['key']) for c in manifest['clients']], report=report)

    def artifact(self, run_id, name):
        if run_id not in self.runs or name not in ('trace.jsonl', 'trace.jsonl.manifest.json', 'config.json'):
            raise ValueError('未知导出文件')
        return self.output / run_id / name


def distribution_preview(spec, field):
    if field not in ('requests','initial_private_tokens','growth_multiplier','external_tokens','output_tokens','gap'):
        raise ValueError('未知分布字段')
    distribution = Distribution(spec, field, count=field not in ('gap','growth_multiplier'),
                                minimum=1 if field == 'requests' else 0)
    rng = random.Random(0)
    samples = [distribution.sample(rng) for _ in range(4096)]
    low, high = min(samples), max(samples)
    width = (high-low)/40 if high > low else 1
    counts = [0]*40 if high > low else [0]
    for value in samples:
        counts[min(len(counts)-1, int((value-low)/width))] += 1
    return dict(cdf=cdf(samples), histogram=[[low+i*width, low+(i+1)*width, n/len(samples)]
                                            for i,n in enumerate(counts)], samples=len(samples),
                density=[[low+i*width, low+(i+1)*width, n/len(samples)/width] for i,n in enumerate(counts)],
                discrete=distribution.count or distribution.kind in ('fixed','discrete') or high==low,
                mean=sum(samples)/len(samples), semantics='fixed independent seed; sampled probability mass after clipping/rounding')


def make_server(host='127.0.0.1', port=8765, output=ROOT/'runs/workbench', static=ROOT/'web/dist'):
    workbench = Workbench(output)
    static = Path(static).resolve()

    class Handler(BaseHTTPRequestHandler):
        def json(self, data, status=200):
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            try:
                origin = self.headers.get('Origin')
                if origin and urlparse(origin).hostname not in ('localhost', '127.0.0.1', '::1'):
                    self.json({'error':'仅接受本地工作台请求'},403)
                    return
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('请求需要 application/json')
                length = int(self.headers.get('Content-Length',0))
                if not 0 < length <= 4*1024*1024:
                    raise ValueError('配置请求大小须在 1 byte 至 4 MiB 之间')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('请求体必须是对象')
                path = urlparse(self.path).path
                options = dict(window=data.get('window',10),task=data.get('task') or None,client=data.get('client') or None)
                if path == '/api/validate':
                    ClientPool(data['config'])
                    result = {'valid': True}
                elif path == '/api/run':
                    result = workbench.run(data['config'], **options)
                elif path == '/api/analyze':
                    result = workbench.report(data['run_id'], **options)
                elif path == '/api/distribution':
                    result = distribution_preview(data['spec'],data['field'])
                else:
                    self.json({'error':'不存在的接口'},404)
                    return
                self.json(result)
            except (BrokenPipeError,ConnectionResetError):
                pass
            except (ValueError,TypeError,KeyError,OSError,OverflowError) as exc:
                self.json({'error':str(exc)},400)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/api/presets':
                configs = [('mixed',ROOT/'examples/config.example.json')]
                configs.extend((p.stem,p) for p in sorted((ROOT/'examples/presets').glob('*.json')))
                self.json([dict(key=k,config=json.loads(p.read_text())) for k,p in configs])
                return
            match = re.fullmatch(r'/api/files/([0-9a-f]{64})/(trace.jsonl|trace.jsonl.manifest.json|config.json)',path)
            if match:
                try:
                    file = workbench.artifact(*match.groups())
                except ValueError as exc:
                    self.json({'error':str(exc)},404)
                    return
                download = True
            else:
                file = (static / (path.lstrip('/') or 'index.html')).resolve()
                if not file.is_relative_to(static):
                    self.json({'error':'不存在的文件'},404)
                    return
                download = False
            if not file.is_file():
                self.json({'error':'请先运行 npm --prefix web ci && npm --prefix web run build'},404)
                return
            self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(file)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(file.stat().st_size))
            if download:
                self.send_header('Content-Disposition',f'attachment; filename="{file.name}"')
            self.end_headers()
            try:
                with file.open('rb') as stream:
                    while chunk := stream.read(1024*1024):
                        self.wfile.write(chunk)
            except (BrokenPipeError,ConnectionResetError):
                pass

        def log_message(self, fmt, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.workbench = workbench
    return server
