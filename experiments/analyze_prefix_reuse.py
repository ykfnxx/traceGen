#!/usr/bin/env python3
"""Measure arrival distributions and historical full-block prefix reuse in existing traces.

No service model, cache eviction, or request completion assumptions are introduced.
Run with the plotting requirements installed; input traces must have generation manifests.
"""
import argparse
from collections import defaultdict
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics


class PrefixHistory:
    """State is updated only after examining the entire current request."""
    def __init__(self):
        self.first_owner = {}
        self.multiple_owners = set()
        self.first_time = {}
        self.first_origin = {}
        self.multiple_origins = set()
        self.parents = {}
        self.by_session = defaultdict(set)

    def observe(self, sid, timestamp, hashes, origin=None):
        origin = sid if origin is None else origin
        same_template_only = other_template = 0
        local = self.by_session[sid]
        counts = dict(reused=0, within=0, cross=0, strictly_earlier=0)
        closed = dict.fromkeys(counts, False)
        previous = None
        if len(set(hashes)) != len(hashes):
            raise ValueError('duplicate identity inside a request')
        for h in hashes:
            if self.parents.setdefault(h, previous) != previous:
                raise ValueError('hash identity has inconsistent prefix parent')
            previous = h
            owner = self.first_owner.get(h)
            flags = dict(reused=owner is not None, within=h in local,
                         cross=owner is not None and (owner != sid or h in self.multiple_owners),
                         strictly_earlier=h in self.first_time and self.first_time[h] < timestamp)
            if flags['reused'] and not flags['within']:
                if self.first_origin[h] != origin or h in self.multiple_origins:
                    other_template += 1
                else:
                    same_template_only += 1
            for key, hit in flags.items():
                if hit and closed[key]:
                    raise ValueError(f'{key} history reuse does not form a continuous prefix')
                counts[key] += hit
                closed[key] |= not hit
        for h in hashes:
            if h not in self.first_origin:
                self.first_origin[h] = origin
            elif self.first_origin[h] != origin:
                self.multiple_origins.add(h)
            if h not in self.first_owner:
                self.first_owner[h] = sid
                self.first_time[h] = timestamp
            elif self.first_owner[h] != sid:
                self.multiple_owners.add(h)
        local.update(hashes)
        counts['cross_extra'] = counts['reused'] - counts['within']
        counts['cross_extra_same_template_only'] = same_template_only
        counts['cross_extra_other_template'] = other_template
        if same_template_only + other_template != counts['cross_extra']:
            raise ValueError('cross-session origin accounting mismatch')
        if counts['reused'] != max(counts['within'], counts['cross']):
            raise ValueError('prefix union accounting mismatch')
        return dict(blocks=len(hashes), **counts)


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(count=0)
    def percentile(q):
        at=(len(values)-1)*q
        low=math.floor(at);high=math.ceil(at)
        return values[low]+(values[high]-values[low])*(at-low)
    mean=statistics.mean(values)
    return dict(count=len(values), min=values[0], mean=mean,
                p50=percentile(.5), p95=percentile(.95), p99=percentile(.99), max=values[-1],
                cv=statistics.pstdev(values)/mean if mean else None)


def aggregate(rows):
    keys=('blocks','reused','within','cross','cross_extra','strictly_earlier',
          'cross_extra_same_template_only','cross_extra_other_template')
    result={key:sum(r[key] for r in rows) for key in keys}
    nonempty=[r for r in rows if r['blocks']]
    result.update(requests=len(rows), nonempty_requests=len(nonempty), empty_requests=len(rows)-len(nonempty))
    for key in keys[1:]:
        result[key+'_rate']=result[key]/result['blocks'] if result['blocks'] else None
    result['requests_with_prefix_reuse']=sum(r['reused']>0 for r in rows)
    result['fully_reused_nonempty_requests']=sum(r['reused']==r['blocks'] for r in nonempty)
    result['mean_request_reuse_fraction_nonempty']=statistics.mean(r['reused']/r['blocks'] for r in nonempty) if nonempty else None
    result['blocks_per_request']=distribution([r['blocks'] for r in rows])
    result['new_blocks_per_request']=distribution([r['blocks']-r['reused'] for r in rows])
    return result


def analyze(path, label, output, index):
    manifest=json.loads(Path(str(path)+'.manifest.json').read_text())
    duration=manifest['config']['duration']
    metadata={s['session_id']:s for s in manifest['sessions']}
    history=PrefixHistory();details=[];fingerprint=hashlib.sha256();prior=-1
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rb') as stream:
        for line in stream:
            fingerprint.update(line);r=json.loads(line)
            t=r['timestamp'];sid=r['session_id']
            if not math.isfinite(t) or not 0<=t<duration or t<prior:
                raise ValueError(f'invalid arrival timeline: {path}')
            prior=t
            details.append(dict(timestamp=t,session_id=sid,source=metadata[sid]['source'],
                                **history.observe(sid,t,r['hash_ids'],
                                                  (metadata[sid]['source'], metadata[sid]['source_record']))))
    if fingerprint.hexdigest()!=manifest['output_sha256_uncompressed']:
        raise ValueError(f'fingerprint mismatch: {path}')
    total=aggregate(details)
    if total['requests']!=manifest['stats']['requests'] or total['blocks']!=manifest['stats']['blocks']:
        raise ValueError('manifest counts mismatch')
    if total['blocks']-total['reused']!=len(history.first_owner):
        raise ValueError('prefix counts disagree with independent global unique-block accounting')
    times=[r['timestamp'] for r in details]
    by_session=defaultdict(list);by_source=defaultdict(list)
    for r in details:
        by_session[r['session_id']].append(r['timestamp']);by_source[r['source']].append(r)
    for sid,meta in metadata.items():
        if len(by_session[sid])!=meta['emitted_requests']:
            raise ValueError('session request accounting mismatch')
    bins=[];position=0
    for start in range(0, math.ceil(duration), 60):
        end=min(start+60,duration);begin=position
        while position<len(details) and details[position]['timestamp']<end:position+=1
        subset=details[begin:position];b=aggregate(subset)
        b.update(start=start,end=end,rps=len(subset)/(end-start))
        b['source_requests']={name:sum(r['source']==name for r in subset) for name in by_source}
        bins.append(b)
    counts=[b['requests'] for b in bins];peak=max(bins,key=lambda b:b['rps'])
    total.update(label=label,path=str(path.resolve()),sha256=fingerprint.hexdigest(),
                 duration=duration,block_size=manifest['config']['block_size'],sessions=len(metadata),
                 unique_hashes=len(history.first_owner),cross_session_unique_hashes=len(history.multiple_owners),
                 mean_rps=len(times)/duration,peak_60s_requests=peak['requests'],peak_60s_rps=peak['rps'],
                 peak_60s_start=peak['start'],empty_60s_bins=counts.count(0),total_60s_bins=len(bins),
                 requests_per_60s=distribution(counts),first_request=times[0] if times else None,
                 last_request=times[-1] if times else None,
                 request_iat_seconds=distribution([b-a for a,b in zip(times,times[1:])]),
                 within_session_iat_seconds=distribution([b-a for t in by_session.values() for a,b in zip(t,t[1:])]),
                 equal_timestamp_adjacent_requests=sum(a==b for a,b in zip(times,times[1:])),
                 per_source={name:aggregate(rows) for name,rows in by_source.items()})
    prefix=f'{index:02d}_{path.stem}'
    for name,rows in [('requests',details),('60s', [{k:v for k,v in b.items() if not isinstance(v,dict)} for b in bins])]:
        if rows:
            with (output/f'{prefix}_{name}.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return dict(summary=total,bins=bins,details=details)


def plot(runs, output, figure_name="arrivals_prefix_reuse"):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager
    font=Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if font.exists():
        font_manager.fontManager.addfont(font)
        plt.rcParams['font.family']=[font_manager.FontProperties(fname=font).get_name()]
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.unicode_minus':False})
    colors=['#337da4','#d47b32','#478d65','#8853a4','#d15460']
    fig,axes=plt.subplots(len(runs),2,figsize=(14,3*len(runs)),squeeze=False,layout='constrained')
    windows = ' / '.join(f'{v/3600:g}' for v in sorted({r['summary']['duration'] for r in runs}))
    sizes = ' / '.join(str(v) for v in sorted({r['summary']['block_size'] for r in runs}))
    fig.suptitle(f'请求到达时序与 block 前缀复用\n{windows} 小时窗口 · block_size={sizes} · 到达顺序历史统计，无缓存淘汰',fontsize=17)
    source_order = list(dict.fromkeys(name for run in runs for name in run['summary']['per_source']))
    source_colors = {name: plt.get_cmap('tab10')(i % 10) for i,name in enumerate(source_order)}
    for i,run in enumerate(runs):
        s=run['summary'];bins=run['bins'];left,right=axes[i]
        xs=np.array([b['start']/60 for b in bins]);width=np.array([(b['end']-b['start'])/60 for b in bins])
        bottom=np.zeros(len(bins))
        for name in source_order:
            counts=np.array([b['source_requests'].get(name,0) for b in bins])
            left.bar(xs,counts,width=width,align='edge',bottom=bottom,label=name,
                     color=source_colors[name],alpha=.85)
            bottom+=counts
        left.axhline(s['mean_rps']*60,color='#555555',linestyle='--',linewidth=1,label='全窗口均值')
        left.set(title=f"{s['label']}：{s['requests']:,} req，峰值 {s['peak_60s_requests']} req/min",ylabel='请求数 / 60 秒')
        left.legend(fontsize=8,loc='upper left')
        # The histories continue across bins; only numerator/denominator are windowed.
        five=[]
        for start in range(0,math.ceil(s['duration']),300):
            subset=[r for r in run['details'] if start<=r['timestamp']<start+300]
            b=aggregate(subset);b['center']=(start+150)/60;five.append(b)
        fx=[b['center'] for b in five]
        global_rate=[100*b['reused_rate'] if b['reused_rate'] is not None else np.nan for b in five]
        within=[100*b['within_rate'] if b['within_rate'] is not None else np.nan for b in five]
        right.plot(fx,global_rate,'o-',color=colors[0],markersize=3,label='全部历史前缀复用')
        right.plot(fx,within,'--',color=colors[1],linewidth=1.8,label='同 session 历史复用')
        right.fill_between(fx,within,global_rate,color=colors[2],alpha=.25,label='跨 session 额外贡献')
        right.set(title=f"block 加权复用率 {100*s['reused_rate']:.2f}%（每 5 分钟统计）",ylabel='可复用前缀 block / 全部 block (%)',ylim=(-3,103))
        right.legend(fontsize=8,loc='lower right')
        for ax in (left,right):
            ax.set_xlim(0,s['duration']/60);ax.set_xlabel('合成时间（分钟）');ax.grid(alpha=.15)
    for ext in ('png','pdf'):
        fig.savefig(output/f'{figure_name}.{ext}',dpi=170,bbox_inches='tight')
    plt.close(fig)


def write_report(runs, output):
    lines=['# 请求时序与 block 前缀复用统计','',
           '范围：最新最小格式试验的合成 trace，非在线服务采集日志。Weka 为 3 个真实源 session 的重采样；SwissAI 为 4025 条真实请求的子集，按 300 秒请求窗口组织；LMSYS 为 4 个人工对话样例，不能代表真实 LMSYS 负载。','',
           '复用率 = 当前请求可从历史匹配的最长连续前缀 block 数之和 / 所有请求完整 block 数之和。按 trace 行顺序更新历史，当前请求不自命中；相同 timestamp 按行顺序处理。历史从空集开始，保持至整个 trace 结束，无容量、TTL 或淘汰限制。这是历史复用机会，不是有限缓存命中率。','',
           '同 session 复用查询该 session 的全部历史；跨 session 额外贡献 = 全部历史复用 - 同 session 复用，因此两者可相加。JSON 另列跨 session 可用前缀（可能与 session 内重叠）以及仅 timestamp 严格更早的全局复用率。空 hash 请求参与时序统计，但不增加 block 分母，也不算完全命中。','',
           '| trace | req | 均值 req/s | 峰值 req/60s | 无请求分钟 | 相邻请求间隔 P50/P95(s) |',
           '|---|---:|---:|---:|---:|---|']
    for run in runs:
        s=run['summary'];d=s['request_iat_seconds']
        lines.append(f"| {s['label']} | {s['requests']:,} | {s['mean_rps']:.3f} | {s['peak_60s_requests']} | {s['empty_60s_bins']}/{s['total_60s_bins']} | {d.get('p50',0):.3f} / {d.get('p95',0):.3f} |")
    lines+=['','| trace | 完整 block 引用数 | 全局前缀复用 | session 内 | 跨 session 额外 | 空 hash 请求 |','|---|---:|---:|---:|---:|---:|']
    for run in runs:
        s=run['summary']
        lines.append(f"| {s['label']} | {s['blocks']:,} | {s['reused_rate']:.2%} | {s['within_rate']:.2%} | {s['cross_extra_rate']:.2%} | {s['empty_requests']}/{s['requests']} ({s['empty_requests']/s['requests']:.1%}) |")
    lines+=['','跨 session 额外复用的来源：','']
    for run in runs:
        s=run['summary']
        if s['cross_extra']:
            lines.append(f"- {s['label']}：{s['cross_extra_same_template_only']} 个 block 引用仅来自重复抽取相同源模板；"
                         f"{s['cross_extra_other_template']} 个引用有此前不同源模板的共享证据。")
    lines+=['','![请求时序与前缀复用](arrivals_prefix_reuse.png)','',
            '逐请求统计和 60 秒分桶 CSV、按来源统计及完整输入 SHA256 见本目录。5 分钟复用曲线只对分子分母分桶，历史不会在桶边界清空。', '',
            '复现：`/tmp/tracegen-plot-venv/bin/python experiments/analyze_prefix_reuse.py --output-dir runs/minimal_trial/distribution_analysis`']
    (output/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--traces',type=Path,nargs='+')
    parser.add_argument('--labels',nargs='+')
    parser.add_argument('--output-dir',type=Path,default=Path('runs/minimal_trial/distribution_analysis'))
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'runs/minimal_trial'
    paths=args.traces or [root/'weka_real_variation'/f'weka_jitter_{j}.jsonl' for j in ('0','0.3','0.6')]+[
        root/'trace_swiss_0.8_3h.jsonl',root/'trace_swiss_0.2_3h.jsonl']
    labels=args.labels or ([p.stem for p in paths] if args.traces else [
        'Weka jitter=0','Weka jitter=0.3','Weka jitter=0.6','SwissAI / LMSYS 样例 80/20','SwissAI / LMSYS 样例 20/80'])
    if len(labels)!=len(paths):parser.error('labels must match traces')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    runs=[]
    for i,(path,label) in enumerate(zip(paths,labels)):
        print(f'Analyzing {label} ...',flush=True)
        run=analyze(path,label,args.output_dir,i);runs.append(run)
        print(json.dumps(run['summary'],ensure_ascii=False),flush=True)
    (args.output_dir/'summary.json').write_text(json.dumps([r['summary'] for r in runs],indent=2,ensure_ascii=False)+'\n')
    plot(runs,args.output_dir)
    if not args.traces:
        plot([runs[1],runs[3],runs[4]],args.output_dir,'overview')
    write_report(runs,args.output_dir)
    print(f'Report: {args.output_dir / "report.md"}')


if __name__=='__main__':
    main()
