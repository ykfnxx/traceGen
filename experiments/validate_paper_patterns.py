#!/usr/bin/env python3
"""Measure emitted traces against explicitly scoped paper statistics; no fitting."""
import argparse
from collections import Counter, defaultdict
import gzip
import json
import math
from pathlib import Path
import statistics as st
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracegen.synthesis import generate


def describe(values):
    xs = sorted(values)
    if not xs:
        return dict(n=0, mean=None, cv=None, p50=None, p95=None, p99=None)
    mean = st.fmean(xs)
    def q(p):
        i = (len(xs)-1)*p
        a = int(i)
        return xs[a] + (xs[min(a+1,len(xs)-1)]-xs[a])*(i-a)
    return dict(n=len(xs), mean=mean, cv=st.pstdev(xs)/mean if mean else None,
                p50=q(.5), p95=q(.95), p99=q(.99))


def ks(a, b):
    ca, cb = Counter(a), Counter(b)
    na = nb = 0
    distance = 0
    for x in sorted(ca.keys() | cb.keys()):
        na += ca[x]; nb += cb[x]
        distance = max(distance, abs(na/len(a)-nb/len(b)))
    return distance


def measure(path, manifest):
    duration = manifest['config']['duration']
    if duration % 3600:
        raise ValueError('paper experiments require complete hours')
    counts = [0]*int(duration)
    ms_counts = Counter()
    sessions = {s['session_id']: s for s in manifest['sessions']}
    clients, observed = Counter(), Counter()
    last_by_session = {}
    iats, gaps, external, inputs, outputs = [], [], [], [], []
    window_iats = defaultdict(list)
    previous = None
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as stream:
        for line in stream:
            r = json.loads(line); t = r['timestamp']; sid = r['session_id']
            counts[int(t)] += 1; ms_counts[int(t*1000)] += 1
            s = sessions[sid]; clients[(s['task'],s['client'])] += 1; observed[sid] += 1
            if previous is not None:
                iats.append(t-previous)
                if int(t//300) == int(previous//300):
                    window_iats[int(t//300)].append(t-previous)
            previous = t
            if sid in last_by_session:
                gaps.append(t-last_by_session[sid]);external.append(r['external_tokens'])
            last_by_session[sid] = t
            inputs.append(r['input_tokens']);outputs.append(r['output_tokens'])
    windows = []
    for start in range(0,len(counts),300):
        x = counts[start:start+300]
        windows.append(dict(start=start, rps=st.fmean(x), count_cv=describe(x)['cv'],
                            iat_cv=describe(window_iats[start//300])['cv'],
                            mssd=st.fmean((b-a)**2 for a,b in zip(x,x[1:]))))
    hours = [sum(sorted(counts[i:i+3600],reverse=True)[:180])/sum(counts[i:i+3600])
             if sum(counts[i:i+3600]) else None for i in range(0,len(counts),3600)]
    n=sum(counts); slots=int(duration*1000); ms_mean=n/slots
    complete=[s for s in sessions.values() if s['emitted_requests']==s['planned_requests'] and s['planned_requests']>1]
    multi=[s for s in sessions.values() if s['planned_requests']>1]
    adjacent=[]
    for i in range(0,len(counts)-300,300):
        a,b=counts[i:i+300],counts[i+300:i+600]
        adjacent.append(dict(start=i,ks=ks(a,b),w1=st.fmean(abs(x-y) for x,y in zip(sorted(a),sorted(b)))))
    return dict(requests=n, observed_clients=len(clients), configured_clients=len(manifest['clients']),
                top29_client_share=sum(sorted(clients.values(),reverse=True)[:29])/n,
                iat_seconds=describe(iats), interturn_seconds=describe(gaps),
                external_tokens=describe(external), input_tokens=describe(inputs),output_tokens=describe(outputs),
                planned_multiturn_count=describe([s['planned_requests'] for s in multi]),
                complete_multiturn_count=describe([s['emitted_requests'] for s in complete]),
                identified_multiturn_request_share=sum(v for v in observed.values() if v>1)/n,
                planned_multiturn_request_share=sum(observed[s['session_id']] for s in multi)/n,
                truncated_sessions=manifest['stats']['truncated_sessions'],
                millisecond_fano=(sum(v*v for v in ms_counts.values())/slots-ms_mean**2)/ms_mean,
                hourly_top5pct_share=hours, windows_300s=windows, adjacent_windows=adjacent,
                trace_sha256=manifest['output_sha256_uncompressed'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configs',type=Path,default=Path('examples/paper_patterns'))
    parser.add_argument('--output-dir',type=Path,default=Path('runs/paper-patterns'))
    parser.add_argument('--seeds',type=int,nargs='+',default=[41,42,43])
    args=parser.parse_args()
    results={}
    for source in sorted(args.configs.glob('*.json')):
        runs=[]
        for seed in args.seeds:
            config=json.loads(source.read_text());config['seed']=seed
            out=args.output_dir/source.stem/str(seed);out.mkdir(parents=True,exist_ok=True)
            path=out/'trace.jsonl.gz'
            manifest=generate(config,path)
            result=measure(path,manifest);result['seed']=seed
            (out/'statistics.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
            runs.append(result)
            print(source.stem,seed,result['requests'],flush=True)
        results[source.stem]=runs
    (args.output_dir/'results.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
