#!/usr/bin/env python3
"""根据 version 3 配置生成 LLM serving 请求 trace。"""
import argparse
import json
from pathlib import Path
import sys
from tracegen.synthesis import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration', type=float)
    parser.add_argument('--block-size', type=int)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--session-rate', type=float)
    parser.add_argument('--arrival-cv', type=float)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        for field in ('duration', 'block_size', 'seed'):
            if getattr(args, field) is not None:
                config[field] = getattr(args, field)
        if args.session_rate is not None:
            config.setdefault('traffic', {})['session_rate'] = args.session_rate
        if args.arrival_cv is not None:
            config.setdefault('traffic', {})['arrival'] = {'distribution': 'gamma', 'cv': args.arrival_cv}
        if args.config.resolve() in (args.output.resolve(), Path(str(args.output.resolve()) + '.manifest.json')):
            raise ValueError('output must not overwrite the configuration')
        manifest = generate(config, args.output)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(manifest['stats'], indent=2))
    print(f'Trace: {args.output}\nManifest: {args.output}.manifest.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
