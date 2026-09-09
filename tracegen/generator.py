"""Session arrival traffic, active-session admission, and empirical request timing."""

from collections import deque
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
from .traffic import arrival_intervals, offer_times, rate_segments


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


def validate_config(config):
    for removed in ("load_scale", "base_session_rate"):
        if removed in config:
            raise ValueError(f"{removed} has been removed; use max_concurrent_sessions, "
                             "session_rate, arrival and bursts (request times are not scaled)")
    integer(config.get("block_size"), "block_size", 1)
    integer(config.get("max_concurrent_sessions"), "max_concurrent_sessions", 1)
    positive(config.get("duration"), "duration")
    integer(config.get("seed", 0), "seed")
    if not isinstance(config.get("arrival", {}), dict):
        raise ValueError("arrival must be an object")
    rate_segments(config)
    next(arrival_intervals(random.Random(0), 1, config.get("arrival", {})))
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

    Session offers use absolute sessions/s with optional burst windows. Full
    sessions wait FIFO for an active-session slot. A slot is released immediately
    after that session's last request arrives, including nested requests. There
    is no request service/completion simulation and no request time scaling.
    """
    validate_config(config)
    duration = config["duration"]
    limit = config["max_concurrent_sessions"]
    seed = config.get("seed", 0)
    arrival = config.get("arrival", {"distribution": "gamma", "cv": 1.0})
    segments = rate_segments(config)
    offers = offer_times(random.Random(f"arrival:{seed}"), segments, arrival)
    next_offer = next(offers, math.inf)
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
    waiting = deque()
    offered_starts = []
    concurrency = [{"timestamp": 0.0, "active_sessions": 0}]
    active = peak = 0
    area = change_time = 0.0

    def change_active(delta, timestamp):
        nonlocal active, peak, area, change_time
        area += active * (timestamp - change_time)
        change_time = timestamp
        active += delta
        if not 0 <= active <= limit:
            raise RuntimeError("session concurrency invariant violated")
        peak = max(peak, active)
        concurrency.append({"timestamp": timestamp, "active_sessions": active})
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
            while next_offer < duration or pending:
                # Existing requests at a timestamp precede new offers. Releasing
                # after the last arrival permits FIFO admission at the same time.
                if pending and pending[0][0] <= next_offer:
                    timestamp, session_num, request_index, instance = heapq.heappop(pending)
                    request = instance.template.requests[request_index]
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
                    if request_index == len(instance.template.requests):
                        change_active(-1, timestamp)
                    else:
                        following = instance.start + instance.template.requests[request_index].offset
                        if following < duration:
                            heapq.heappush(pending, (following, session_num, request_index, instance))
                else:
                    timestamp = next_offer
                    waiting.append(timestamp)
                    offered_starts.append(timestamp)
                    next_offer = next(offers, math.inf)
                while waiting and active < limit:
                    offered = waiting.popleft()
                    dataset = chooser.choices(datasets, weights=weights, k=1)[0]
                    index = chooser.randrange(len(dataset.offsets))
                    template = dataset.get(index)
                    session_num = len(sessions)
                    session_id = f"s{session_num:08d}"
                    metadata = {
                        "session_id": session_id, "source": dataset.name,
                        "source_record": index, "source_session_id": template.source_id,
                        "offered_start": offered, "start": timestamp,
                        "start_delay": timestamp - offered,
                        "end": timestamp + template.requests[-1].offset,
                        "template_requests": len(template.requests), "emitted_requests": 0,
                        "template_duration": template.requests[-1].offset,
                        "hash_id_scope": template.scope,
                    }
                    if not math.isfinite(metadata["end"]):
                        raise ValueError("session timeline overflow")
                    sessions.append(metadata)
                    instance = Instance(session_id, template, timestamp, metadata)
                    change_active(1, timestamp)
                    heapq.heappush(pending, (timestamp, session_num, 0, instance))
        area += active * (duration - change_time)
        concurrency.append({"timestamp": duration, "active_sessions": active})
        stats["sessions"] = len(sessions)
        stats["truncated_sessions"] = sum(s["emitted_requests"] < s["template_requests"]
                                           for s in sessions)
        stats["omitted_requests_at_end"] = sum(s["template_requests"] - s["emitted_requests"]
                                                for s in sessions)
        stats["actual_rps"] = stats["requests"] / duration
        stats["actual_session_rate"] = len(sessions) / duration
        stats["unique_templates"] = len({(s["source"], s["source_record"]) for s in sessions})
        stats.update({
            "offered_sessions": len(offered_starts), "pending_sessions_at_end": len(waiting),
            "delayed_sessions": sum(s["start_delay"] > 0 for s in sessions),
            "mean_start_delay_seconds": sum(s["start_delay"] for s in sessions) / max(1, len(sessions)),
            "max_start_delay_seconds": max((s["start_delay"] for s in sessions), default=0.0),
            "peak_concurrent_sessions": peak, "mean_concurrent_sessions": area / duration,
            "active_sessions_at_end": active,
        })
        manifest = {
            "schema_version": 2, "generator": "tracegen-v2", "config": config,
            "timestamp_unit": "seconds", "window": "[0, duration)",
            "timing": "request timestamp = admitted session start + unchanged reference offset",
            "concurrency_semantics": "first arrival through last arrival; no request processing time",
            "admission": "FIFO session starts; wait at max_concurrent_sessions",
            "session_rate_segments": segments, "offered_session_starts": offered_starts,
            "session_concurrency": concurrency,
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
