#!/usr/bin/env python3
"""Synthesize three intensities and independently check every emitted Weka request."""

import argparse
from collections import Counter
import hashlib
import itertools
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tracegen.generator import build_datasets, generate


def require(condition, message):
    if not condition:
        raise ValueError(message)


def lcp(a, b):
    for index, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return index
    return min(len(a), len(b))


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0}
    return {"count": len(values), "min": values[0], "median": statistics.median(values),
            "p95": values[math.ceil(0.95 * len(values)) - 1], "max": values[-1]}


def audit_source(path, block_size):
    """Independent raw source reader: no generator adapter/hash functions used."""
    references = []
    main_count = nested_count = 0
    for line in Path(path).open():
        if not line.strip():
            continue
        record = json.loads(line)
        source_size = record["block_size"]
        require(block_size % source_size == 0, "unsupported source resolution")
        requests = []

        def visit(items, nested=False):
            nonlocal main_count, nested_count
            for item in items:
                if "requests" in item:
                    visit(item["requests"], True)
                else:
                    requests.append(item)
                    if nested:
                        nested_count += 1
                    else:
                        main_count += 1

        visit(record["requests"])
        requests.sort(key=lambda r: r["t"])
        origin = requests[0]["t"]
        prior = []
        reference = []
        for request in requests:
            require(len(request["hash_ids"]) == request["in"] // source_size,
                    "in and source hashes disagree")
            common_blocks = lcp(prior, request["hash_ids"]) // (block_size // source_size)
            reference.append((request["t"] - origin, request["in"] // block_size, common_blocks))
            prior = request["hash_ids"]
        references.append(reference)
    return references, {
        "sessions": len(references), "main_requests": main_count,
        "nested_requests": nested_count, "requests": main_count + nested_count,
        "requests_per_session": distribution([len(r) for r in references]),
        "session_duration_seconds": distribution([r[-1][0] for r in references]),
        "request_iat_seconds": distribution([b[0] - a[0] for r in references
                                               for a, b in zip(r, r[1:])]),
    }


def validate_trace(path, manifest, references):
    metadata = {s["session_id"]: s for s in manifest["sessions"]}
    indices = Counter()
    previous_hashes = {}
    last_time = -1
    rows = blocks = prefix_checks = 0
    fingerprint = hashlib.sha256()
    scale = manifest["config"]["load_scale"]
    duration = manifest["config"]["duration"]
    bins = [0] * math.ceil(duration / 300)
    for line in path.open():
        fingerprint.update(line.encode())
        row = json.loads(line)
        require(set(row) == {"timestamp", "hash_ids", "session_id"}, "wrong output schema")
        timestamp, hashes, sid = row["timestamp"], row["hash_ids"], row["session_id"]
        require(last_time <= timestamp < duration, "out-of-order or out-of-window timestamp")
        require(all(type(h) is int and 0 <= h < 2**64 for h in hashes), "invalid uint64 hash")
        last_time = timestamp
        info = metadata[sid]
        index = indices[sid]
        offset, count, expected_lcp = references[info["source_record"]][index]
        require(len(hashes) == count, f"incomplete block coverage at {sid}/{index}")
        require(math.isclose(timestamp, info["start"] + offset / scale, abs_tol=1e-9),
                f"wrong session timing at {sid}/{index}")
        if index:
            require(lcp(previous_hashes[sid], hashes) == expected_lcp,
                    f"prefix relation changed at {sid}/{index}")
            prefix_checks += 1
        indices[sid] += 1
        if indices[sid] == info["emitted_requests"]:
            previous_hashes.pop(sid, None)
        else:
            previous_hashes[sid] = hashes
        rows += 1
        blocks += count
        bins[min(int(timestamp // 300), len(bins) - 1)] += 1
    for sid, info in metadata.items():
        require(indices[sid] == info["emitted_requests"], "manifest request count mismatch")
        reference = references[info["source_record"]]
        expected = sum(info["start"] + r[0] / scale < duration for r in reference)
        require(indices[sid] == expected, "unexpected request missing inside duration")
    require(rows == manifest["stats"]["requests"], "wrong total requests")
    require(blocks == manifest["stats"]["blocks"], "wrong total blocks")
    require(fingerprint.hexdigest() == manifest["output_sha256_uncompressed"], "checksum mismatch")
    return {"validated_requests": rows, "validated_blocks": blocks,
            "validated_consecutive_prefix_pairs": prefix_checks,
            "requests_per_300s": bins, "checks": "passed"}


def compare_scales(lower_path, lower_scale, upper_path, upper_scale):
    count = 0
    with lower_path.open() as low, upper_path.open() as high:
        for a, b in itertools.zip_longest(low, high):
            if a is None:
                break
            require(b is not None, "higher intensity lost baseline requests")
            a, b = json.loads(a), json.loads(b)
            require(a["session_id"] == b["session_id"], "scale changed session selection")
            require(a["hash_ids"] == b["hash_ids"], "scale changed request blocks")
            require(math.isclose(a["timestamp"] * lower_scale, b["timestamp"] * upper_scale,
                                 abs_tol=1e-9), "scale changed baseline time")
            count += 1
    return {"lower_scale": lower_scale, "upper_scale": upper_scale,
            "identical_baseline_requests": count, "checks": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runs/weka")
    parser.add_argument("--duration", type=float, default=3600)
    parser.add_argument("--base-session-rate", type=float, default=0.01)
    parser.add_argument("--block-size", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    config = {
        "block_size": args.block_size, "duration": args.duration, "load_scale": 1.0,
        "base_session_rate": args.base_session_rate, "seed": args.seed,
        "arrival": {"distribution": "gamma", "cv": 1.5},
        "datasets": [{"name": "weka-agentic", "path": str(args.source.resolve()),
                      "format": "weka", "weight": 1.0}],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("Indexing and auditing original Weka sessions ...", flush=True)
    datasets = build_datasets(config)
    references, source_stats = audit_source(args.source, args.block_size)
    report = {"python": platform.python_version(), "source": str(args.source.resolve()),
              "source_sha256": datasets[0].sha256, "source_stats": source_stats,
              "config": config, "runs": [], "scale_checks": [],
              "code_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in [ROOT / "tracegen/sources.py", ROOT / "tracegen/generator.py",
                                        ROOT / "experiments/run_weka.py"]}}
    paths = []
    for scale in (0.5, 1.0, 2.0):
        print(f"Generating load_scale={scale} ...", flush=True)
        run_config = dict(config, load_scale=scale)
        path = args.output_dir / f"weka_scale_{scale:g}.jsonl"
        start = time.perf_counter()
        manifest = generate(run_config, path, datasets=datasets)
        elapsed = time.perf_counter() - start
        print(f"Validating {manifest['stats']['requests']} requests ...", flush=True)
        validation = validate_trace(path, manifest, references)
        result = {"load_scale": scale, "path": str(path.resolve()),
                  "generation_seconds": elapsed, "bytes": path.stat().st_size,
                  "sha256": manifest["output_sha256_uncompressed"],
                  **manifest["stats"], **validation}
        report["runs"].append(result)
        paths.append((path, scale))
        print(json.dumps(result), flush=True)
    for (a, low), (b, high) in zip(paths, paths[1:]):
        report["scale_checks"].append(compare_scales(a, low, b, high))
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"All checks passed. Report: {args.output_dir / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
