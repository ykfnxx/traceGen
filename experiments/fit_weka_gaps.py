"""离线拟合 Weka 主链到达间隔；需要 NumPy，不参与 trace 生成。"""
import argparse
import json,math
from pathlib import Path
import numpy as np
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args=parser.parse_args()
gaps=[]
for line in args.input.open():
 rows=[r for r in json.loads(line)['requests'] if 'api_time' in r and 'in' in r]
 gaps.extend(b['t']-a['t'] for a,b in zip(rows,rows[1:]))
x=np.array(gaps)
if not len(x) or not np.all(np.isfinite(x)) or np.any(x<=0):
 raise ValueError('lognormal fit requires finite positive main-chain arrival gaps')
y=np.log(x)
models=[]
for k in (1,2,3):
 mu=np.quantile(y,np.linspace(.15,.95,k)) if k>1 else np.array([y.mean()]); sd=np.full(k,y.std());w=np.ones(k)/k
 for _ in range(300):
  l=-np.log(sd)-.5*((y[:,None]-mu)/sd)**2+np.log(w); z=l.max(axis=1);r=np.exp(l-z[:,None]);r/=r.sum(axis=1)[:,None];n=r.sum(axis=0);newmu=(r*y[:,None]).sum(axis=0)/n;newsd=np.sqrt((r*(y[:,None]-newmu)**2).sum(axis=0)/n);neww=n/len(y)
  delta=max(abs(newmu-mu));mu,sd,w=newmu,newsd,neww
  if delta<1e-7:break
 l=-np.log(sd)-.5*((y[:,None]-mu)/sd)**2+np.log(w)-.5*np.log(2*np.pi)-y[:,None];z=l.max(axis=1);ll=(z+np.log(np.exp(l-z[:,None]).sum(axis=1))).sum()
 comps=[dict(distribution='lognormal',mean=float(np.exp(a+b*b/2)),cv=float(np.sqrt(np.expm1(b*b)))) for a,b in zip(mu,sd)]
 spec=dict(distribution='mixture',weights=w.tolist(),components=comps)
 rng=np.random.default_rng(42);idx=rng.choice(k,200000,p=w);sample=np.exp(rng.normal(mu[idx],sd[idx]));sample.sort(); xx=np.sort(x);F=np.searchsorted(sample,xx,side='right')/len(sample);ks=max(np.max(abs(F-np.arange(1,len(x)+1)/len(x))),np.max(abs(F-np.arange(len(x))/len(x))))
 model=dict(k=k,bic=float(-2*ll+(3*k-1)*math.log(len(y))),ks=float(ks),spec=spec,quantiles=np.quantile(sample,[.5,.9,.95,.99]).tolist(),tail={str(t):float(np.mean(sample>=t)) for t in [60,120,300,600,3600]})
 models.append(model)
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(models,indent=2)+'\n')
for model in models: print({k:model[k] for k in ('k','bic','ks','quantiles','tail')})
