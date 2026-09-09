#!/usr/bin/env python3
"""Generate a trace: python3 generate.py --config examples/demo.json --output runs/demo.jsonl"""

import argparse
import json
from pathlib import Path
import sys

from tracegen.generator import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="JSONL or .jsonl.gz")
    parser.add_argument("--block-size", type=int)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--max-concurrent-sessions", type=int)
    parser.add_argument("--session-rate", type=float)
    parser.add_argument("--arrival-cv", type=float, help="Gamma session-offer IAT CV; 0 is uniform")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        for key in ("block_size", "duration", "max_concurrent_sessions", "session_rate", "seed"):
            if getattr(args, key) is not None:
                config[key] = getattr(args, key)
        if args.arrival_cv is not None:
            config["arrival"] = {"distribution": "gamma", "cv": args.arrival_cv}
        for dataset in config["datasets"]:
            dataset["path"] = str((args.config.resolve().parent / dataset["path"]).resolve())
        if args.output.resolve() == args.config.resolve():
            raise ValueError("output must not overwrite the configuration")
        manifest = generate(config, args.output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(manifest["stats"], indent=2))
    print(f"Trace: {args.output}\nManifest: {args.output}.manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
