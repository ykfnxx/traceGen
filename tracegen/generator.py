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
from .traffic import arrival_intervals, rate_segments
from .streams import SessionOffers, independent, rate_scales, source_segments
from .variation import BlockVariation, validate_jitter


@dataclass
class Instance:
    session_id: str
    template: SessionTemplate
    start: float
    manifest: dict
    hashes: dict = field(default_factory=dict)
    jitter: float = 0.0
    seed: int = 0
    variation: BlockVariation | None = field(default=None, init=False)

    def __post_init__(self):
        self.manifest['block_variation'] = dict(reference_new_blocks=0, synthetic_new_blocks=0,
                                               requests_with_changed_new_blocks=0)
        if self.jitter:
            if self.template.scope != "local":
                raise ValueError("new_block_jitter requires local hash_id_scope; "
                                 "set new_block_jitter=0 for global reference identities")
            self.variation = BlockVariation(self.template.prefix_segments,
                                            self.session_id, self.seed, self.jitter)
            self.manifest["block_variation"] = self.variation.stats

    def materialize(self, values):
        if self.variation is not None:
            return self.variation.materialize(values)
        before = len(self.hashes)
        if self.template.scope == "global":
            self.hashes.update(dict.fromkeys(values))
            new = len(self.hashes)-before
            self.manifest['block_variation']['reference_new_blocks'] += new
            self.manifest['block_variation']['synthetic_new_blocks'] += new
            return list(values)
        # Local source identities only prove sharing inside this sampled session.
        # Rekeying the already prefix-dependent digest isolates independent copies.
        out = []
        for value in values:
            if value not in self.hashes:
                self.hashes[value] = digest(["tracegen-instance-v1", self.session_id, value])
            out.append(self.hashes[value])
        new = len(self.hashes)-before
        self.manifest['block_variation']['reference_new_blocks'] += new
        self.manifest['block_variation']['synthetic_new_blocks'] += new
        return out


def validate_config(config):
    for removed in ("load_scale", "base_session_rate"):
        if removed in config:
            raise ValueError(f"{removed} has been removed; use max_concurrent_sessions, "
                             "session_rate, arrival and bursts (request times are not scaled)")
    integer(config.get("block_size"), "block_size", 1)
    positive(config.get("duration"), "duration")
    integer(config.get("seed", 0), "seed")
    validate_jitter(config.get("new_block_jitter", 0.3))
    if not isinstance(config.get("arrival", {}), dict):
        raise ValueError("arrival must be an object")
    if not isinstance(config.get("datasets"), list) or not config["datasets"]:
        raise ValueError("datasets must be a nonempty list")
    if config.get("max_concurrent_sessions") is not None or not independent(config):
        integer(config.get("max_concurrent_sessions"), "max_concurrent_sessions", 1)
    if independent(config):
        if any(k in config for k in ('session_rate', 'arrival', 'bursts')):
            raise ValueError('independent streams require traffic settings inside each dataset')
        for spec in config['datasets']:
            if not isinstance(spec.get('traffic'), dict):
                raise ValueError('every dataset must specify traffic for independent streams')
            if 'weight' in spec:
                raise ValueError('independent streams use traffic.session_rate and rate_scale, not weight')
            scale = positive(spec.get('rate_scale', 1), 'rate_scale')
            source_segments(config, spec, scale)
            arrival = spec['traffic'].get('arrival', {})
            if not isinstance(arrival, dict):
                raise ValueError('dataset traffic.arrival must be an object')
            next(arrival_intervals(random.Random(0), 1, arrival))
    else:
        rate_segments(config)
        next(arrival_intervals(random.Random(0), 1, config.get('arrival', {})))
    for spec in config["datasets"]:
        declared_size = integer(spec.get("block_size", config["block_size"]), "dataset block_size", 1)
        if declared_size != config["block_size"]:
            raise ValueError("dataset block_size must match generation block_size; rerun prepare.py")
        if spec.get("hash_id_scope", "local") not in ("local", "global"):
            raise ValueError("dataset hash_id_scope must be local or global")
        jitter = validate_jitter(spec.get("new_block_jitter", config.get("new_block_jitter", .3)))
        if spec.get("hash_id_scope", "local") == "global" and jitter:
            raise ValueError("set new_block_jitter=0 for global reference identities")
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
    config = dict(config, new_block_jitter=config.get("new_block_jitter", 0.3))
    validate_config(config)
    if any("request_ratio" in spec for spec in config["datasets"]):
        raise ValueError("use experiments/run_mixed.py --config to calibrate request_ratio first")
    duration = config["duration"]
    limit = config.get("max_concurrent_sessions") or math.inf
    seed = config.get("seed", 0)
    output = Path(output).resolve()
    manifest_path = Path(str(output) + ".manifest.json")
    source_paths = {Path(spec["path"]).resolve() for spec in config["datasets"]}
    if output in source_paths or manifest_path in source_paths:
        raise ValueError("output must not overwrite a source dataset")
    datasets = build_datasets(config) if datasets is None else datasets
    if [(d.name, d.path, d.block_size, d.weight, d.format, d.new_block_jitter, d.scope) for d in datasets] != [
        (s["name"], Path(s["path"]).resolve(), config["block_size"],
         s.get("weight", 1), s.get("format", "session_jsonl"), s.get('new_block_jitter'), s.get('hash_id_scope', 'local')) for s in config["datasets"]
    ]:
        raise ValueError("preloaded datasets do not match configuration")
    offers = SessionOffers(config, datasets, rate_scales(config, datasets))
    segments = offers.segments
    next_event = offers.next()
    next_offer = next_event[0]
    blocks_by_source = {d.name:0 for d in datasets}
    sessions = []
    pending = []
    waiting = deque()
    offered_starts = []
    offered_by_source = dict.fromkeys((d.name for d in datasets), 0)
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
                    if not hashes:
                        raise RuntimeError("empty hash_ids must be excluded before session sampling")
                    row = {"timestamp": timestamp, "hash_ids": hashes,
                           "session_id": instance.session_id}
                    line = json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
                    stream.write(line)
                    fingerprint.update(line.encode())
                    stats["requests"] += 1
                    stats["blocks"] += len(hashes)
                    blocks_by_source[instance.manifest['source']] += len(hashes)
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
                    waiting.append(next_event)
                    offered_starts.append(timestamp)
                    if offers.independent:
                        offered_by_source[datasets[next_event[1]].name] += 1
                    next_event = offers.next()
                    next_offer = next_event[0]
                while waiting and active < limit:
                    event = waiting.popleft()
                    offered = event[0]
                    dataset, index, source_session_id = offers.admit(event)
                    template = dataset.get(index)
                    session_num = len(sessions)
                    session_id = source_session_id or f"s{session_num:08d}"
                    metadata = {
                        "session_id": session_id, "source": dataset.name,
                        "source_record": dataset.record_numbers[index], "source_session_id": template.source_id,
                        "offered_start": offered, "start": timestamp,
                        "start_delay": timestamp - offered,
                        "end": timestamp + template.requests[-1].offset,
                        "template_requests": len(template.requests), "emitted_requests": 0,
                        "template_duration": template.requests[-1].offset,
                        "hash_id_scope": template.scope,
                        "new_block_jitter": (dataset.new_block_jitter if dataset.new_block_jitter is not None
                                             else config['new_block_jitter']),
                    }
                    if not math.isfinite(metadata["end"]):
                        raise ValueError("session timeline overflow")
                    sessions.append(metadata)
                    instance = Instance(session_id, template, timestamp, metadata,
                                        jitter=metadata['new_block_jitter'], seed=seed)
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
        varied = any(s['new_block_jitter'] for s in sessions)
        if varied:
            for key in ("reference_new_blocks", "synthetic_new_blocks",
                        "requests_with_changed_new_blocks"):
                stats[key] = sum(s["block_variation"][key] for s in sessions)
        manifest = {
            "schema_version": 2, "generator": "tracegen-v2", "config": config,
            "timestamp_unit": "seconds", "window": "[0, duration)",
            "timing": "request timestamp = admitted session start + unchanged reference offset",
            "concurrency_semantics": "first arrival through last arrival; no request processing time",
            "admission": "FIFO session starts; wait at max_concurrent_sessions",
            "arrival_mode": "independent_sources" if offers.independent else "shared_clock",
            "session_rate_segments": segments, "offered_session_starts": offered_starts,
            "session_concurrency": concurrency,
            "hash_scheme": ("blake2b-64 prefix chain v2; varied segments per local instance"
                            if varied else
                            "blake2b-64 prefix chain v2; local identities rekeyed per instance"),
            "hash_coverage": ("complete blocks of the synthetic context after new-block variation"
                              if varied else
                              "complete blocks of the reference request context only"),
            "block_variation": {
                "new_block_jitter": config["new_block_jitter"],
                "dataset_overrides": {d.name:d.new_block_jitter for d in datasets if d.new_block_jitter is not None},
                "sampling": "uniform factor per request's new suffix; stochastic rounding per segment",
                "minimum_segment_blocks": 1,
                "preserved": "request endpoints, prefix branches, and already materialized segments",
            },
            "boundary": "empty at time zero; requests at/after duration omitted",
            "input_filter": "exclude empty hash_ids and all-empty sessions before sampling; "
                            "rebase first retained arrival to zero; preserve retained request intervals",
            "output_sha256_uncompressed": fingerprint.hexdigest(),
            "sources": [{"name": d.name, "path": str(d.path), "format": d.format,
                         "sha256": d.sha256, "session_records": len(d.offsets),
                         "normalization": d.normalization, "filtering": d.filter_stats} for d in datasets],
            "source_mix": [{"name": d.name,
                            **({"rate_scale": config["datasets"][i].get("rate_scale", 1.0),
                                "offered_sessions": offered_by_source[d.name]} if offers.independent
                               else {"weight": d.weight}),
                            "sessions":sum(s['source']==d.name for s in sessions),
                            "requests":sum(s['emitted_requests'] for s in sessions if s['source']==d.name),
                            "block_references":blocks_by_source[d.name]}
                           for i, d in enumerate(datasets)],
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
