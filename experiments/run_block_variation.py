#!/usr/bin/env python3
"""Compare new-block jitter on one 3h timeline; audit topology independently."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tracegen.generator import build_datasets, generate
from tracegen.frontends.weka import prepare_weka


def require(ok, message):
    if not ok:
        raise ValueError(message)


def distribution(values):
    v = sorted(values)
    if not v:
        return {"count": 0}
    return dict(count=len(v), min=v[0], mean=sum(v)/len(v),
                p50=v[math.ceil(.5*len(v))-1], p95=v[math.ceil(.95*len(v))-1],
                p99=v[math.ceil(.99*len(v))-1], max=v[-1])


def audit(path, manifest):
    """Hash-independent topology signature from observable request prefixes.

    Label terminal nodes by request index. Suppress unary nodes without request
    endpoints. The resulting canonical shape captures all branching/ancestry
    and non-adjacent reuse, but permits segment lengths to vary.
    """
    parents, owners, depths = {}, {}, {}
    terminals = defaultdict(list)
    indices = Counter()
    frequency = Counter()
    shared = set()
    metrics = []
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for line in stream:
            sha.update(line)
            row = json.loads(line)
            sid, hashes = row['session_id'], row['hash_ids']
            root = ('root', sid)
            parents.setdefault(root, None)
            parent, new, entered_new = root, 0, False
            require(len(hashes) == len(set(hashes)), 'duplicate hash in request')
            for position, h in enumerate(hashes):
                if owners.setdefault(h, sid) != sid:
                    shared.add(h)
                require(parents.setdefault(h, parent) == parent, 'inconsistent hash parent')
                require(depths.setdefault(h, position) == position, 'inconsistent hash depth')
                if frequency[h]:
                    require(not entered_new, 'history reuse after new prefix')
                else:
                    entered_new = True
                    new += 1
                frequency[h] += 1
                parent = h
            terminals[parent].append(indices[sid])
            indices[sid] += 1
            metrics.append(dict(session_id=sid, timestamp=row['timestamp'],
                                blocks=len(hashes), new_blocks=new))
    require(sha.hexdigest() == manifest['output_sha256_uncompressed'], 'output SHA256 mismatch')
    require(dict(indices) == {s['session_id']:s['emitted_requests'] for s in manifest['sessions']},
            'per-session request counts differ from manifest')
    child_signatures = defaultdict(list)
    topology = {}
    # Parents are encountered before children when reading complete prefixes.
    for node, parent in reversed(parents.items()):
        children = sorted(child_signatures.pop(node, []))
        endpoints = terminals.get(node, [])
        if parent is not None and len(children) == 1 and not endpoints:
            signature = children[0]
        else:
            signature = hashlib.sha256(json.dumps([endpoints, children],
                                                  separators=(',', ':')).encode()).hexdigest()
        if parent is None:
            topology[node[1]] = signature
        else:
            child_signatures[parent].append(signature)
    unique, refs = len(frequency), sum(frequency.values())
    require(refs == manifest['stats']['blocks'], 'total block count differs')
    if manifest['config']['new_block_jitter']:
        require(unique == manifest['stats']['synthetic_new_blocks'], 'new-block accounting mismatch')
    return dict(requests=len(metrics), sessions=len(indices), unique_hashes=unique,
                block_references=refs, historical_repeat_fraction=(refs-unique)/refs if refs else 0,
                cross_session_unique_hashes=len(shared),
                blocks_per_request=distribution([r['blocks'] for r in metrics]),
                new_blocks_per_request=distribution([r['new_blocks'] for r in metrics]),
                zero_new_requests=sum(r['new_blocks']==0 for r in metrics)), metrics, topology


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'runs/weka_block_variation')
    parser.add_argument('--duration', type=float, default=10800)
    parser.add_argument('--block-size', type=int, default=128)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    normalized = args.output_dir/'weka.sessions.jsonl'
    preparation = prepare_weka(args.source,normalized,args.block_size)
    config = dict(block_size=args.block_size, duration=args.duration, max_concurrent_sessions=32,
                  session_rate=.01, seed=args.seed, arrival=dict(distribution='gamma', cv=1.5),
                  datasets=[dict(name='weka-agentic', path=str(normalized.resolve()),
                                 format='session_jsonl', weight=1.)])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    datasets = build_datasets(config)
    result = dict(config=config, source_sha256=preparation['sources'][0]['sha256'], runs=[],
                  code_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [ROOT/'tracegen/variation.py', ROOT/'tracegen/generator.py',
                                         ROOT/'tracegen/sources.py', Path(__file__)]})
    baseline_metrics = baseline_topology = baseline_manifest = None
    for jitter in (0, .3, .6):
        name = f'jitter_{jitter:g}'
        path = args.output_dir/f'weka_{name}.jsonl'
        start = time.perf_counter()
        print(f'Generating {name} ...', flush=True)
        manifest = generate(dict(config, new_block_jitter=jitter), path, datasets=datasets)
        elapsed = time.perf_counter()-start
        print(f'Auditing {name} ...', flush=True)
        stats, metrics, topology = audit(path, manifest)
        if baseline_metrics is None:
            baseline_metrics, baseline_topology, baseline_manifest = metrics, topology, manifest
        require(topology == baseline_topology, 'request/branch topology changed')
        require([(m['session_id'],m['timestamp']) for m in metrics] ==
                [(m['session_id'],m['timestamp']) for m in baseline_metrics], 'arrival timeline changed')
        require(manifest['session_concurrency'] == baseline_manifest['session_concurrency'],
                'session concurrency changed')
        require(stats['cross_session_unique_hashes'] == 0, 'unexpected cross-session reuse')
        increased = decreased = changed = 0
        ratios = []
        for current, old in zip(metrics, baseline_metrics):
            a,b = current['new_blocks'], old['new_blocks']
            require((a==0) == (b==0), 'zero/new request classification changed')
            changed += a != b
            increased += a > b
            decreased += a < b
            if b:
                ratios.append(a/b)
        if jitter:
            require(changed == manifest['stats']['requests_with_changed_new_blocks'], 'changed count mismatch')
            require(manifest['stats']['reference_new_blocks'] == sum(m['new_blocks'] for m in baseline_metrics),
                    'reference new-block count mismatch')
            require(increased > 0 and decreased > 0, 'no two-sided length variation observed')
        stats.update(new_block_jitter=jitter, path=str(path.resolve()),
                     sha256=manifest['output_sha256_uncompressed'], generation_seconds=elapsed,
                     changed_requests=changed, increased_requests=increased, decreased_requests=decreased,
                     changed_request_fraction=changed/max(1,len(metrics)),
                     new_block_ratio=distribution(ratios), checks='passed')
        result['runs'].append(stats)
        (args.output_dir/f'{name}_requests.json').write_text(json.dumps(metrics,separators=(',',':'))+'\n')
        print(json.dumps(stats), flush=True)
    (args.output_dir/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    lines = ['# 新增 block 随机扰动试验', '',
             f'时长 {args.duration:g} 秒，block size {args.block_size}，并发上限 32，seed {args.seed}。', '',
             '| jitter | 请求数 | 新增 block 均值 | P50 / P95 / P99 | 新增量变化请求 | 增大 / 减小 | 跨 session 共享 hash |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for r in result['runs']:
        d=r['new_blocks_per_request']
        lines.append(f"| {r['new_block_jitter']} | {r['requests']} | {d['mean']:.2f} | "
                     f"{d['p50']} / {d['p95']} / {d['p99']} | {r['changed_requests']} "
                     f"({r['changed_request_fraction']:.1%}) | {r['increased_requests']} / "
                     f"{r['decreased_requests']} | {r['cross_session_unique_hashes']} |")
    lines += ['', '全部检查通过：请求时间线及并发一致；输出前缀树去除无端点的单支中间节点后，'
              '其请求端点标签和分叉拓扑与无扰动基线相同；历史完全重复请求保持零新增；'
              '父 hash 和深度一致，无请求内重复 hash，历史复用构成连续前缀。', '',
              '此校验独立读取输出，不调用扰动器的树压缩或采样函数。它证明可见复用结构保持，'
              '不要求扰动后的前缀长度与原始数据相等。历史重复引用比例不等于有限缓存命中率。', '',
              '随机取整以及每个前缀段至少一个 block 的约束使短请求可能不发生长度变化；'
              '因此 jitter 是倍率幅度，不能解释为最终新增量的精确 CV。', '',
              '明细与指纹见 report.json，各请求新增数见 jitter_*_requests.json。']
    (args.output_dir/'report.md').write_text('\n'.join(lines)+'\n')
    print(f'All checks passed. Report: {args.output_dir / "report.md"}', flush=True)


if __name__ == '__main__':
    main()
