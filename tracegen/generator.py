"""Empirical session templates + Gamma/Weibull session arrivals + time scaling."""

from dataclasses import dataclass, field
import gzip
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import random
import tempfile

from .sources import Dataset, SessionTemplate, digest, integer, positive


@dataclass
class Instance:
    session_id: str
    template: SessionTemplate
    start: float
    manifest: dict
    hashes: dict = field(default_factory=dict)

    def materialize(self, values):
        if self.template.scope == "global":
            return list(values)
        # Local source identities only prove sharing inside this sampled session.
        # Rekeying the already prefix-dependent digest isolates independent copies.
        out = []
        for value in values:
            if value not in self.hashes:
                self.hashes[value] = digest(["tracegen-instance-v1", self.session_id, value])
            out.append(self.hashes[value])
        return out


def arrival_intervals(rng, rate, arrival):
    distribution = arrival.get("distribution", "gamma")
    if distribution == "gamma":
        cv = positive(arrival.get("cv", 1.0), "arrival.cv")
        shape = 1.0 / (cv * cv)
        scale = cv * cv / rate
        if not math.isfinite(shape) or not math.isfinite(scale) or min(shape, scale) <= 0:
            raise ValueError("arrival.cv is numerically out of range")
        sample = lambda: rng.gammavariate(shape, scale)
    elif distribution == "weibull":
        shape = positive(arrival.get("shape", 1.0), "arrival.shape")
        try:
            scale = 1.0 / (rate * math.gamma(1.0 + 1.0 / shape))
        except (OverflowError, ValueError) as exc:
            raise ValueError("arrival.shape is numerically out of range") from exc
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("arrival.shape is numerically out of range")
        sample = lambda: rng.weibullvariate(scale, shape)
    else:
        raise ValueError("arrival.distribution must be gamma or weibull")
    while True:
        interval = sample()
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("arrival sampling produced a nonpositive/nonfinite interval")
        yield interval


def validate_config(config):
    integer(config.get("block_size"), "block_size", 1)
    for name in ("duration", "base_session_rate"):
        positive(config.get(name), name)
    positive(config.get("load_scale", 1.0), "load_scale")
    integer(config.get("seed", 0), "seed")
    if not math.isfinite(config["duration"] * config.get("load_scale", 1.0)):
        raise ValueError("duration * load_scale must be finite")
    if not isinstance(config.get("arrival", {}), dict):
        raise ValueError("arrival must be an object")
    if not isinstance(config.get("datasets"), list) or not config["datasets"]:
        raise ValueError("datasets must be a nonempty list")
    names = [item["name"] for item in config["datasets"]]
    if len(names) != len(set(names)):
        raise ValueError("dataset names must be unique")


def build_datasets(config):
    validate_config(config)
    return [Dataset(spec, config["block_size"]) for spec in config["datasets"]]


def generate(config, output, *, datasets=None):
    """Write sorted JSONL and a manifest; duration is the output interval [0,D).

    Sample on baseline time [0,D*load_scale), then divide ALL times by load_scale.
    Separate RNG streams guarantee common session plans across scale experiments.
    There are no sessions before time zero; right-boundary requests are counted
    in the manifest and omitted. The generator never fabricates missing blocks.
    """
    validate_config(config)
    duration = config["duration"]
    load_scale = config.get("load_scale", 1.0)
    horizon = duration * load_scale
    seed = config.get("seed", 0)
    arrival = config.get("arrival", {"distribution": "gamma", "cv": 1.0})
    intervals = arrival_intervals(random.Random(f"arrival:{seed}"),
                                  config["base_session_rate"], arrival)
    next_start = next(intervals)  # Validate arrival parameters before opening outputs.
    chooser = random.Random(f"template:{seed}")
    output = Path(output).resolve()
    manifest_path = Path(str(output) + ".manifest.json")
    source_paths = {Path(spec["path"]).resolve() for spec in config["datasets"]}
    if output in source_paths or manifest_path in source_paths:
        raise ValueError("output must not overwrite a source dataset")
    datasets = build_datasets(config) if datasets is None else datasets
    if [(d.name, d.path, d.block_size, d.weight, d.format) for d in datasets] != [
        (s["name"], Path(s["path"]).resolve(), config["block_size"],
         s.get("weight", 1), s.get("format", "session_jsonl")) for s in config["datasets"]
    ]:
        raise ValueError("preloaded datasets do not match configuration")
    weights = [d.weight for d in datasets]
    sessions = []
    pending = []
    stats = {"requests": 0, "blocks": 0, "empty_hash_requests": 0,
             "first_timestamp": None, "last_timestamp": None}
    fingerprint = hashlib.sha256()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    manifest_temp = None
    try:
        opener = gzip.open if output.suffix == ".gz" else open
        with opener(temporary, "wt", encoding="utf-8", newline="\n") as stream:
            while next_start < horizon or pending:
                if next_start < horizon and (not pending or next_start <= pending[0][0]):
                    dataset = chooser.choices(datasets, weights=weights, k=1)[0]
                    index = chooser.randrange(len(dataset.offsets))
                    template = dataset.get(index)
                    session_num = len(sessions)
                    session_id = f"s{session_num:08d}"
                    metadata = {
                        "session_id": session_id, "source": dataset.name,
                        "source_record": index, "source_session_id": template.source_id,
                        "start": next_start / load_scale,
                        "template_requests": len(template.requests), "emitted_requests": 0,
                        "template_duration": template.requests[-1].offset,
                        "hash_id_scope": template.scope,
                    }
                    sessions.append(metadata)
                    instance = Instance(session_id, template, next_start, metadata)
                    heapq.heappush(pending, (next_start, session_num, 0, instance))
                    old_start = next_start
                    next_start += next(intervals)
                    if next_start <= old_start or not math.isfinite(next_start):
                        raise ValueError("session arrival clock overflow or lost precision")
                    continue
                base_time, session_num, request_index, instance = heapq.heappop(pending)
                request = instance.template.requests[request_index]
                timestamp = base_time / load_scale
                # A strict half-open horizon, including floating-point division.
                if timestamp < duration:
                    hashes = instance.materialize(request.hashes)
                    row = {"timestamp": timestamp, "hash_ids": hashes,
                           "session_id": instance.session_id}
                    line = json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
                    stream.write(line)
                    fingerprint.update(line.encode())
                    stats["requests"] += 1
                    stats["blocks"] += len(hashes)
                    stats["empty_hash_requests"] += not hashes
                    if stats["first_timestamp"] is None:
                        stats["first_timestamp"] = timestamp
                    stats["last_timestamp"] = timestamp
                    instance.manifest["emitted_requests"] += 1
                request_index += 1
                if request_index < len(instance.template.requests):
                    following = instance.start + instance.template.requests[request_index].offset
                    if following < horizon:
                        heapq.heappush(pending, (following, session_num, request_index, instance))
        stats["sessions"] = len(sessions)
        stats["truncated_sessions"] = sum(s["emitted_requests"] < s["template_requests"]
                                           for s in sessions)
        stats["omitted_requests_at_end"] = sum(s["template_requests"] - s["emitted_requests"]
                                                for s in sessions)
        stats["actual_rps"] = stats["requests"] / duration
        stats["actual_session_rate"] = len(sessions) / duration
        stats["unique_templates"] = len({(s["source"], s["source_record"]) for s in sessions})
        manifest = {
            "schema_version": 1, "generator": "tracegen-v1", "config": config,
            "timestamp_unit": "seconds", "window": "[0, duration)",
            "timing": "empirical session offsets; sampled session starts; all times / load_scale",
            "hash_scheme": "blake2b-64 prefix chain; local identities rekeyed per instance",
            "hash_coverage": "complete blocks of the reference request context only",
            "boundary": "empty at time zero; requests at/after duration omitted",
            "output_sha256_uncompressed": fingerprint.hexdigest(),
            "sources": [{"name": d.name, "path": str(d.path), "format": d.format,
                         "sha256": d.sha256, "session_records": len(d.offsets)} for d in datasets],
            "stats": stats, "sessions": sessions,
        }
        fd, manifest_temp = tempfile.mkstemp(prefix=f".{manifest_path.name}.", dir=output.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, output)
        os.replace(manifest_temp, manifest_path)
        return manifest
    finally:
        for path in (temporary, manifest_temp):
            if path and os.path.exists(path):
                os.unlink(path)
