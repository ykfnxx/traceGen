#!/usr/bin/env python3
"""Compare session concurrency caps and burst offers on real Weka templates."""

import argparse
from collections import Counter
import hashlib
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
from tracegen.frontends.weka import prepare_weka


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
        requests = [r for r in requests if r['in'] // block_size > 0]
        requests.sort(key=lambda r: r["t"])
        if not requests:
            references.append([])  # Preserve the original source record index.
            continue
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
        "session_duration_seconds": distribution([r[-1][0] for r in references if r]),
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
    duration = manifest["config"]["duration"]
    live = set()
    observed_peak = 0
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
        if index == 0:
            live.add(sid)
            observed_peak = max(observed_peak, len(live))
            require(len(live) <= manifest["config"]["max_concurrent_sessions"], "concurrency exceeded")
        offset, count, expected_lcp = references[info["source_record"]][index]
        require(len(hashes) == count, f"incomplete block coverage at {sid}/{index}")
        require(math.isclose(timestamp, info["start"] + offset, abs_tol=1e-9),
                f"wrong session timing at {sid}/{index}")
        if index:
            require(lcp(previous_hashes[sid], hashes) == expected_lcp,
                    f"prefix relation changed at {sid}/{index}")
            prefix_checks += 1
        indices[sid] += 1
        if indices[sid] == info["template_requests"]:
            live.remove(sid)
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
        expected = sum(info["start"] + r[0] < duration for r in reference)
        require(indices[sid] == expected, "unexpected request missing inside duration")
        require(info["start"] >= info["offered_start"], "session started before offer")
        require(math.isclose(info["end"], info["start"] + reference[-1][0]), "wrong session release time")
    occupancy = sum(min(duration, s["end"]) - s["start"] for s in metadata.values()) / duration
    require(math.isclose(occupancy, manifest["stats"]["mean_concurrent_sessions"], abs_tol=1e-9),
            "concurrency area mismatch")
    require(len(live) == manifest["stats"]["active_sessions_at_end"], "wrong final active sessions")
    require(observed_peak == manifest["stats"]["peak_concurrent_sessions"], "wrong concurrency peak")
    require(manifest["stats"]["offered_sessions"] == len(metadata) + manifest["stats"]["pending_sessions_at_end"],
            "session offer accounting mismatch")
    require(rows == manifest["stats"]["requests"], "wrong total requests")
    require(blocks == manifest["stats"]["blocks"], "wrong total blocks")
    require(fingerprint.hexdigest() == manifest["output_sha256_uncompressed"], "checksum mismatch")
    return {"validated_requests": rows, "validated_blocks": blocks,
            "validated_consecutive_prefix_pairs": prefix_checks,
            "validated_peak_concurrent_sessions": observed_peak,
            "requests_per_300s": bins, "checks": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runs/weka_v2")
    parser.add_argument("--duration", type=float, default=10800)
    parser.add_argument("--session-rate", type=float, default=0.01)
    parser.add_argument("--block-size", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    normalized = args.output_dir/'weka.sessions.jsonl'
    preparation = prepare_weka(args.source,normalized,args.block_size)
    config = {
        "block_size": args.block_size, "duration": args.duration, "max_concurrent_sessions": 32,
        "session_rate": args.session_rate, "seed": args.seed,
        "new_block_jitter": 0,
        "arrival": {"distribution": "gamma", "cv": 1.5},
        "datasets": [{"name": "weka-agentic", "path": str(normalized.resolve()),
                      "format": "session_jsonl", "weight": 1.0}],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("Indexing and auditing original Weka sessions ...", flush=True)
    datasets = build_datasets(config)
    references, source_stats = audit_source(args.source, args.block_size)
    report = {"python": platform.python_version(), "source": str(args.source.resolve()),
              "source_sha256": preparation['sources'][0]['sha256'], "source_stats": source_stats,
              "schema_version": 2, "config": config, "runs": [],
              "code_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in [ROOT / "tracegen/sources.py", ROOT / "tracegen/generator.py", ROOT / "tracegen/traffic.py",
                                        ROOT / "experiments/run_weka.py"]}}
    scenarios = [
        ("cap8", dict(config, max_concurrent_sessions=8)),
        ("cap32", dict(config, max_concurrent_sessions=32)),
        ("cap32_burst", dict(config, max_concurrent_sessions=32, bursts=[
            {"start": args.duration / 3, "duration": args.duration / 18, "session_rate": args.session_rate * 5}])),
    ]
    for name, run_config in scenarios:
        print(f"Generating {name} ...", flush=True)
        path = args.output_dir / f"weka_{name}.jsonl"
        start = time.perf_counter()
        manifest = generate(run_config, path, datasets=datasets)
        elapsed = time.perf_counter() - start
        print(f"Validating {manifest['stats']['requests']} requests ...", flush=True)
        validation = validate_trace(path, manifest, references)
        result = {"scenario": name, "max_concurrent_sessions": run_config["max_concurrent_sessions"],
                  "path": str(path.resolve()),
                  "generation_seconds": elapsed, "bytes": path.stat().st_size,
                  "sha256": manifest["output_sha256_uncompressed"],
                  **manifest["stats"], **validation}
        report["runs"].append(result)
        print(json.dumps(result), flush=True)
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"All checks passed. Report: {args.output_dir / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
