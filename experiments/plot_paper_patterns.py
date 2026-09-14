#!/usr/bin/env python3
"""Plot measured statistics and explicitly scoped paper references."""
import argparse
import json
from pathlib import Path
import statistics as st


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=Path,default=Path('runs/paper-patterns/results.json'))
    parser.add_argument('--output',type=Path,default=Path('runs/paper-patterns/comparison.png'))
    args=parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    results=json.loads(args.results.read_text())
    fig, axes=plt.subplots(2,3,figsize=(15,8),layout='constrained')
    def bars(ax, names, labels, metric, title, target=None, band=None):
        vals=[[metric(r) for r in results[name]] for name in names]
        means=[st.fmean(v) for v in vals]
        ax.bar(labels,means,color=['#8197aa','#28a58b','#e7b04a'][:len(names)],width=.55)
        ax.errorbar(range(len(names)),means,yerr=[[m-min(v) for m,v in zip(means,vals)],
                    [max(v)-m for m,v in zip(means,vals)]],fmt='none',ecolor='#182c3c',capsize=5)
        if target is not None: ax.axhline(target,color='#cc5360',ls='--',label='Paper: approximate reference')
        if band is not None: ax.axhspan(*band,alpha=.12,color='#cc5360',label='Paper: selected peak-hour range')
        ax.set_title(title);ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        if target is not None or band is not None:ax.legend(fontsize=8)
    bars(axes[0,0],['head_uniform','head_skewed'],['Uniform','Exponent 1.6'],lambda r:100*r['top29_client_share'],'Top 29 client request share (%)',target=90)
    bars(axes[0,1],['multiturn_session10','multiturn_request10'],['10% sessions','3.08% sessions'],lambda r:100*r['identified_multiturn_request_share'],'Identified multi-turn request share (%)',target=10)
    bars(axes[0,2],['multiturn_request10'],['Complete conversations'],lambda r:r['complete_multiturn_count']['mean'],'Requests per complete conversation',target=3.5)
    names=['arrival_poisson','arrival_spikes','arrival_cv3_clients1']
    labels=['Poisson','Periodic spikes','IAT CV=3']
    bars(axes[1,0],names,labels,lambda r:100*st.fmean(r['hourly_top5pct_share']),'Top 5% seconds: hourly request share (%)',band=(15,18))
    names=['arrival_cv3_clients1','arrival_cv3_clients64'];labels=['1 client','64 clients']
    bars(axes[1,1],names,labels,lambda r:r['iat_seconds']['cv'],'Aggregate request IAT CV (client CV=3)')
    bars(axes[1,2],names,labels,lambda r:st.fmean(w['count_cv'] for w in r['windows_300s']),'Mean 5-minute count CV (1-second bins)')
    fig.suptitle('traceGen: measured traces, seeds 41/42/43 (error bars: min-max)\nReference moments only; rates, shapes and unspecified parameters are assumptions',fontsize=13)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(args.output,dpi=160)
    plt.close(fig)


if __name__=='__main__':main()
