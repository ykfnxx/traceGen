#!/usr/bin/env python3
"""Synthesize the independent dataset files listed in a JSON configuration.

Only nonempty requests participate. Optional ratios target emitted requests.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tracegen.calibration import calibrate, count_replay
from tracegen.streams import rate_scales, independent
from tracegen.generator import build_datasets, generate
from experiments.analyze_prefix_reuse import analyze, plot


def verify_schedule(path, manifest, prediction, datasets):
    """Check predicted admissions and each retained reference request's timestamp."""
    expected=prediction['sessions']
    actual=[{k:s[k] for k in expected[0]} for s in manifest['sessions']] if expected else manifest['sessions']
    if actual!=expected:
        raise ValueError('count-only calibration replay disagrees with generation')
    if prediction['request_counts']!={s['name']:s['requests'] for s in manifest['source_mix']}:
        raise ValueError('predicted request mix differs from output')
    if prediction['peak_concurrent_sessions']!=manifest['stats']['peak_concurrent_sessions']:
        raise ValueError('predicted concurrency differs from output')
    refs={d.name:dict(zip(d.record_numbers,d.timelines)) for d in datasets}
    metadata={s['session_id']:s for s in manifest['sessions']}
    seen=Counter();active=set();peak=0
    with path.open() as stream:
        for line in stream:
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


def run_config(config_path, output):
    """Run any number of independently configured sources, optionally calibrating."""
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text())
    for spec in config['datasets']:
        spec['path'] = str((config_path.parent / spec['path']).resolve())
    if not independent(config):
        raise ValueError('--config requires a traffic object for each dataset')
    print('Indexing independent datasets ...', flush=True)
    ds = build_datasets(config)
    targets = [s.get('request_ratio') for s in config['datasets']]
    settings = config.get('calibration', {})
    calibrated = any(t is not None for t in targets)
    if calibrated:
        if any(t is None for t in targets):
            raise ValueError('request_ratio must be supplied for every source or none')
        print(f'Calibrating final request proportions: {targets} ...', flush=True)
        result = calibrate(config, ds, targets, settings.get('tolerance', .01),
                           settings.get('max_iterations', 150))
        for spec, scale in zip(config['datasets'], result['weights']):
            spec['rate_scale'] = scale
        prediction = result['prediction']
    else:
        prediction = count_replay(config, ds, rate_scales(config, ds))
        result = None
    # Saved config contains effective rate scales and can be replayed as-is.
    for spec in config['datasets']:
        spec.pop('request_ratio', None)
    config.pop('calibration', None)
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'mixed.jsonl'
    if config_path in (output/'mixed.config.json', output/'report.json', path):
        raise ValueError('output directory would overwrite the input configuration')
    (output/'mixed.config.json').write_text(json.dumps(config, indent=2)+'\n')
    print(f'Generating mixed: expected counts {prediction["request_counts"]} ...', flush=True)
    manifest = generate(config, path, datasets=ds)
    verify_schedule(path, manifest, prediction, ds)
    analysis = analyze(path, 'mixed', output, 0)
    plot([analysis], output, 'mixed_arrivals_prefix_reuse')
    total = manifest['stats']['requests']
    report = dict(config=config, target_unit='emitted nonempty requests',
                  calibration=({k:v for k,v in result.items() if k != 'prediction'} if result else None),
                  runs=[dict(name='mixed', trace_file=path.name, source_mix=manifest['source_mix'],
                             actual_request_shares=[s['requests']/total if total else 0 for s in manifest['source_mix']],
                             stats=manifest['stats'], analysis=analysis['summary'], checks='passed')])
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(request_counts=prediction['request_counts'], stats=manifest['stats']), indent=2))
    print(f'All checks passed: {output / "report.json"}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True,
                        help='JSON listing dataset files and per-source traffic settings')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'runs/mixed',
                        help='directory for trace, effective config, report and plots')
    args = parser.parse_args()
    run_config(args.config, args.output_dir.resolve())


if __name__ == '__main__':
    main()
