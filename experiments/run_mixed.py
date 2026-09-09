#!/usr/bin/env python3
"""Calibrate three separate datasets to final request ratios and synthesize mixed traces.

Default inputs: full local normalized Weka, real SwissAI subset, LMSYS fixture.
Only nonempty requests participate. Ratios refer to emitted requests in [0, duration).
"""
import argparse
from collections import Counter
import json
import hashlib
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tracegen.calibration import calibrate
from tracegen.generator import build_datasets, generate
from experiments.analyze_prefix_reuse import analyze, plot


def verify_schedule(path, manifest, prediction, datasets):
    """Check predicted admissions and each retained reference request's timestamp."""
    expected=prediction['sessions']
    actual=[{k:s[k] for k in expected[0]} for s in manifest['sessions']]
    if actual!=expected:
        raise ValueError('count-only calibration replay disagrees with generation')
    if prediction['request_counts']!={s['name']:s['requests'] for s in manifest['source_mix']}:
        raise ValueError('predicted request mix differs from output')
    if prediction['peak_concurrent_sessions']!=manifest['stats']['peak_concurrent_sessions']:
        raise ValueError('predicted concurrency differs from output')
    refs={d.name:dict(zip(d.record_numbers,d.timelines)) for d in datasets}
    metadata={s['session_id']:s for s in manifest['sessions']}
    seen=Counter();active=set();peak=0
    for line in path.open():
        row=json.loads(line)
        if set(row)!={'timestamp','hash_ids','session_id'} or not row['hash_ids']:
            raise ValueError('output schema or nonempty hash boundary violated')
        sid=row['session_id'];meta=metadata[sid]
        if not seen[sid]:active.add(sid);peak=max(peak,len(active))
        offset=refs[meta['source']][meta['source_record']][seen[sid]]
        if row['timestamp']!=meta['start']+offset:
            raise ValueError('retained request interval changed')
        seen[sid]+=1
        if seen[sid]==meta['template_requests']:active.remove(sid)
    if dict(seen)!={s['session_id']:s['emitted_requests'] for s in manifest['sessions']}:
        raise ValueError('per-session emitted request counts mismatch')
    if peak!=manifest['stats']['peak_concurrent_sessions'] or len(active)!=manifest['stats']['active_sessions_at_end']:
        raise ValueError('independent concurrency audit failed')


def save_report(results, profiles, args):
    output=args.output_dir
    report=dict(duration=args.duration,block_size=args.block_size,seed=args.seed,
                max_concurrent_sessions=args.max_concurrent_sessions,session_rate=args.session_rate,
                target_unit='emitted nonempty requests',tolerance=args.tolerance,
                sources=profiles,runs=results,
                code_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                             [ROOT/'tracegen/sources.py',ROOT/'tracegen/generator.py',ROOT/'tracegen/calibration.py',
                              ROOT/'experiments/run_mixed.py',ROOT/'experiments/analyze_prefix_reuse.py']},
                limitations='Default LMSYS input is an artificial fixture; SwissAI is a real subset organized as request windows. '
                            'Reuse is historical opportunity with no cache eviction. Calibrated weights are tied to the seed and traffic configuration.')
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# 三来源请求配比合成试验','',
           f'时长 {args.duration/3600:g} h，block_size={args.block_size}，seed={args.seed}，并发上限 {args.max_concurrent_sessions}，session 候选率 {args.session_rate:g}/s，Gamma CV={args.arrival_cv:g}。','',
           '各数据集独立输入；按最终有效请求数校准 session 抽样权重。先排除空 hash 请求和全空 session，保留有效请求间隔并将首个有效请求归零。校准计入 FIFO 准入和窗口末尾截断，不按请求配额额外删减有效请求。','',
           f'允许误差：每个来源的请求占比绝对误差 ≤ {args.tolerance*100:g} 个百分点。','',
           '| 来源 | 输入 session | 可采样 session | 输入请求 | 有效请求 | 剔除空 hash 请求 |','|---|---:|---:|---:|---:|---:|']
    for p in profiles:
        f=p['filtering']
        lines.append(f"| {p['name']} | {f['input_sessions']} | {p['eligible_sessions']} | {f['input_requests']} | {p['eligible_requests']} | {f['excluded_empty_requests']} |")
    lines+=['','比例顺序为 **Weka / SwissAI / LMSYS 样例**。LMSYS 默认输入是人工对话样例；SwissAI 是真实请求子集，窗口不是真实用户 session。','',
            '| 场景 | 目标请求比例 | 实际请求比例 | 输出请求 | block 引用 | 前缀复用率 | 峰值并发 |','|---|---|---|---:|---:|---:|---:|']
    for r in results:
        s=r['analysis'];share=lambda x:' / '.join(f'{v*100:.2f}%' for v in x)
        lines.append(f"| [{r['name']}]({r['trace_file']}) | {share(r['target_request_shares'])} | {share(r['actual_request_shares'])} | {s['requests']:,} | {s['blocks']:,} | {s['reused_rate']:.2%} | {r['stats']['peak_concurrent_sessions']} |")
    lines+=['','| 场景 | 各来源实际请求数 | 各来源 session 数 | 各来源 block 引用占比 | 校准次数 | 最大误差（百分点） |','|---|---|---|---|---:|---:|']
    for r in results:
        m=r['source_mix'];total=sum(s['block_references'] for s in m)
        block_shares = " / ".join(format(s["block_references"]/total, ".2%") for s in m)
        lines.append(f"| {r['name']} | {' / '.join(str(s['requests']) for s in m)} | {' / '.join(str(s['sessions']) for s in m)} | {block_shares} | {r['calibration_iterations']} | {r['max_absolute_error']*100:.3f} |")
    lines+=['','所有输出均通过：无空 hash 请求、来源请求数与预测完全一致、有效请求间隔不变、并发重建一致、截止计数一致、hash 指纹及前缀链/复用统计检查。', '',
            '前缀复用率按 block 加权：历史最长连续前缀 block 数之和 / 全部 block 引用数。图中的请求数按 60 秒分桶，复用率按 5 分钟分桶；历史不在桶边界清空。', '',
            '![三来源混合时序与复用](mixed_arrivals_prefix_reuse.png)','',
            '每个场景的 `.config.json` 是已校准、可直接用于 generate.py 的配置；校准过程另存 `.calibration.json`。输入文件和代码 SHA256、实际 session/request/block 配比见 report.json。修改时长、流量、并发、随机种子或输入文件后需重新校准。']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weka',type=Path,default=ROOT/'runs/mixed_sources_3h/inputs/weka.jsonl')
    parser.add_argument('--swissai',type=Path,default=ROOT/'runs/minimal_trial/swissai.jsonl')
    parser.add_argument('--lmsys',type=Path,default=ROOT/'runs/minimal_trial/lmsys.fixture.jsonl')
    parser.add_argument('--lmsys-name',default='lmsys-fixture')
    parser.add_argument('--ratios',type=float,nargs=3,help='target emitted request ratios: Weka SwissAI LMSYS; default runs 3 scenarios')
    parser.add_argument('--duration',type=float,default=10800)
    parser.add_argument('--block-size',type=int,default=128)
    parser.add_argument('--session-rate',type=float,default=.2)
    parser.add_argument('--max-concurrent-sessions',type=int,default=32)
    parser.add_argument('--arrival-cv',type=float,default=1.5)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--tolerance',type=float,default=.01,help='absolute request-share tolerance; .01 = 1 percentage point')
    parser.add_argument('--max-iterations',type=int,default=150)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'runs/mixed_sources_3h')
    args=parser.parse_args();args.output_dir=args.output_dir.resolve();args.output_dir.mkdir(parents=True,exist_ok=True)
    config=dict(block_size=args.block_size,duration=args.duration,max_concurrent_sessions=args.max_concurrent_sessions,
                session_rate=args.session_rate,seed=args.seed,new_block_jitter=.3,
                arrival=dict(distribution='gamma',cv=args.arrival_cv),datasets=[
                    dict(name='weka-agentic',path=str(args.weka.resolve()),weight=1.),
                    dict(name='swissai',path=str(args.swissai.resolve()),weight=1.,hash_id_scope='global',new_block_jitter=0),
                    dict(name=args.lmsys_name,path=str(args.lmsys.resolve()),weight=1.)])
    print('Indexing and filtering separate datasets ...',flush=True)
    ds=build_datasets(config)
    profiles=[dict(name=d.name,path=str(d.path),sha256=d.sha256,filtering=d.filter_stats,
                   eligible_sessions=len(d.offsets),eligible_requests=sum(map(len,d.timelines)),normalization=d.normalization) for d in ds]
    print(json.dumps([{k:v for k,v in p.items() if k!='normalization'} for p in profiles],indent=2),flush=True)
    scenarios=[('custom',args.ratios)] if args.ratios else [
        ('mixed_40_40_20',[.4,.4,.2]),('agentic_70_20_10',[.7,.2,.1]),('chat_10_20_70',[.1,.2,.7])]
    results=[];analyses=[]
    for i,(name,targets) in enumerate(scenarios):
        print(f'Calibrating {name}: target requests {targets} ...',flush=True)
        result=calibrate(config,ds,targets,args.tolerance,args.max_iterations)
        (args.output_dir/f'{name}.calibration.json').write_text(json.dumps({k:v for k,v in result.items() if k!='prediction'},indent=2)+'\n')
        for spec,d,w in zip(config['datasets'],ds,result['weights']):spec['weight']=w;d.weight=w
        cfg=json.loads(json.dumps(config))
        (args.output_dir/f'{name}.config.json').write_text(json.dumps(cfg,indent=2)+'\n')
        window = '3h' if args.duration == 10800 else f'{args.duration:g}s'
        path=args.output_dir/f'{name}_{window}.jsonl'
        print(f'Generating {name}: expected counts {result["prediction"]["request_counts"]} ...',flush=True)
        started=time.perf_counter();manifest=generate(cfg,path,datasets=ds)
        elapsed=time.perf_counter()-started
        verify_schedule(path,manifest,result['prediction'],ds)
        actual=[s['requests']/manifest['stats']['requests'] for s in manifest['source_mix']]
        if max(abs(a-b) for a,b in zip(actual,result['target_request_shares']))>args.tolerance:
            raise ValueError('generated request mix misses target tolerance')
        print(f'Analyzing {name} ...',flush=True)
        analysis=analyze(path,name,args.output_dir,i);analyses.append(analysis)
        results.append(dict(name=name,trace_file=path.name,config_file=f'{name}.config.json',
                            target_request_shares=result['target_request_shares'],actual_request_shares=actual,
                            max_absolute_error=result['max_absolute_error'],calibrated_session_weights=result['weights'],
                            calibration_iterations=result['iterations'],generation_seconds=elapsed,
                            source_mix=manifest['source_mix'],stats=manifest['stats'],analysis=analysis['summary'],checks='passed'))
        print(json.dumps({k:v for k,v in results[-1].items() if k not in ('analysis','stats')},ensure_ascii=False),flush=True)
        save_report(results,profiles,args)
    plot(analyses,args.output_dir,'mixed_arrivals_prefix_reuse')
    print(f'All checks passed: {args.output_dir / "report.md"}',flush=True)


if __name__=='__main__':
    main()
