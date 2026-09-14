"""从 task/client 配置合成 session，再按 serving 到达时间流式归并。"""

import gzip
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import random
import tempfile
from urllib.parse import quote

from .clientpool import ClientPool
from .profiles import stable_seed


def block_hash(*parts):
    return f"{stable_seed('prefix-v3', *parts):032x}"


class Session:
    def __init__(self, pool, client, ordinal, start):
        task = client.task
        self.id = f"{quote(task['key'], safe='')}:{quote(client.key, safe='')}:{ordinal:08d}"
        seed = stable_seed(pool.seed, task["key"], client.key, ordinal, "session-v1")
        streams = {name: random.Random(stable_seed(seed, name))
                   for name in ("structure", "growth", "output", "timing")}
        p = task["profile"]
        count = p["requests"].sample(streams["structure"])
        private = p["initial_private_tokens"].sample(streams["structure"])
        multiplier = p["growth_multiplier"].sample(streams["structure"])
        choices = task["prefix_groups"]
        group = None
        if choices:
            chosen = streams["structure"].choices(choices, [c.get("weight", 1) for c in choices])[0]
            group = pool.groups[chosen["key"]]
        public = group["tokens"] if group else 0
        scope = group.get("scope", "task") if group else None
        namespace = ([task["key"], client.key] if scope == "client" else
                     [task["key"]] if scope == "task" else [])
        self.public_identity = [group["key"], public, scope, namespace] if group else None
        self.public_blocks = public // pool.block_size
        self.block_size = pool.block_size
        self.seed = seed
        self.plan = []
        offset, tokens, external, previous_output = 0.0, public + private, private, 0
        for index in range(count):
            if index:
                offset += p["gap"].sample(streams["timing"])
                increment = multiplier * p["external_tokens"].sample(streams["growth"])
                if not math.isfinite(increment):
                    raise ValueError("session token growth out of range")
                external = int(math.floor(increment + .5))
                tokens += previous_output + external
            output = p["output_tokens"].sample(streams["output"])
            if not math.isfinite(start + offset):
                raise ValueError("session timestamp out of range")
            self.plan.append(dict(timestamp=start + offset, request_index=index,
                                  input_tokens=tokens, output_tokens=output, external_tokens=external))
            previous_output = output
        self.metadata = dict(session_id=self.id, task=task["key"], client=client.key,
                             ordinal=ordinal, session_seed=str(seed), start=start,
                             end=self.plan[-1]["timestamp"], planned_requests=count,
                             emitted_requests=0, initial_private_tokens=private,
                             growth_multiplier=multiplier,
                             prefix_group=group["key"] if group else None, public_prefix_tokens=public)

    def requests(self, duration):
        hashes = []
        parent = block_hash("root", self.block_size)
        for record in self.plan:
            if record["timestamp"] >= duration:
                break
            for i in range(len(hashes), record["input_tokens"] // self.block_size):
                identity = (["public", self.public_identity] if i < self.public_blocks else
                            ["private", self.id, str(self.seed), self.public_identity])
                parent = block_hash(parent, identity, i)
                hashes.append(parent)
            self.metadata["emitted_requests"] += 1
            yield dict(record, session_id=self.id, hash_ids=list(hashes))


def events(pool):
    """只保留每个 client 的下个发起事件及每个活跃 session 的下个请求。"""
    heap = []
    for client in pool.clients:
        offers = client.offers(pool.seed)
        timestamp = next(offers, None)
        if timestamp is not None:
            heapq.heappush(heap, (timestamp, 0, client.task["key"], client.key, 0,
                                  (client, offers)))
    while heap:
        timestamp, kind, task_key, client_key, ordinal, payload = heapq.heappop(heap)
        if kind == 0:
            client, offers = payload
            session = Session(pool, client, ordinal, timestamp)
            requests = session.requests(pool.duration)
            row = next(requests)
            heapq.heappush(heap, (timestamp, 1, task_key, client_key, ordinal,
                                  (session, requests, row)))
            following = next(offers, None)
            if following is not None:
                heapq.heappush(heap, (following, 0, task_key, client_key, ordinal + 1,
                                      (client, offers)))
        else:
            session, requests, row = payload
            yield session, row
            following = next(requests, None)
            if following is not None:
                heapq.heappush(heap, (following["timestamp"], 1, task_key, client_key, ordinal,
                                      (session, requests, following)))


def generate(config, output):
    pool = ClientPool(config)
    output = Path(output).resolve()
    manifest_path = Path(str(output) + ".manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = []
    sessions = {}
    stats = dict(requests=0, blocks=0, empty_requests=0, input_tokens=0, output_tokens=0)
    task_stats = {t["key"]: dict(sessions=0, requests=0, blocks=0) for t in pool.tasks}
    fingerprint = hashlib.sha256()
    try:
        fd, trace_temp = tempfile.mkstemp(prefix=".tracegen-", dir=output.parent)
        temporary.append(trace_temp)
        with os.fdopen(fd, "wb") as raw:
            compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) if output.suffix == ".gz" else None
            stream = compressed or raw
            try:
                for session, row in events(pool):
                    if session.id not in sessions:
                        sessions[session.id] = session.metadata
                        task_stats[session.metadata["task"]]["sessions"] += 1
                    stats["requests"] += 1
                    stats["blocks"] += len(row["hash_ids"])
                    stats["empty_requests"] += not row["hash_ids"]
                    stats["input_tokens"] += row["input_tokens"]
                    stats["output_tokens"] += row["output_tokens"]
                    source = task_stats[session.metadata["task"]]
                    source["requests"] += 1
                    source["blocks"] += len(row["hash_ids"])
                    if not config.get("output", {}).get("request_metadata", True):
                        row = {k: row[k] for k in ("timestamp", "session_id", "hash_ids")}
                    encoded = (json.dumps(row, separators=(",", ":"), ensure_ascii=False,
                                          allow_nan=False) + "\n").encode("utf-8")
                    fingerprint.update(encoded)
                    stream.write(encoded)
            finally:
                if compressed:
                    compressed.close()
        session_list = list(sessions.values())
        concurrency_events = {}
        for session in session_list:
            stop = min(session["end"], pool.duration)
            if stop > session["start"]:
                for t, delta in ((session["start"], 1), (stop, -1)):
                    concurrency_events[t] = concurrency_events.get(t, 0) + delta
        active, peak, area, previous = 0, 0, 0, 0
        concurrency = []
        for t, delta in sorted(concurrency_events.items()):
            area += active * (t - previous)
            active += delta
            peak = max(peak, active)
            concurrency.append(dict(timestamp=t, active=active))
            previous = t
        stats.update(sessions=len(sessions), actual_rps=stats["requests"] / pool.duration,
                     actual_session_rate=len(sessions) / pool.duration,
                     truncated_sessions=sum(s["emitted_requests"] < s["planned_requests"] for s in session_list),
                     omitted_requests_at_end=sum(s["planned_requests"] - s["emitted_requests"] for s in session_list),
                     peak_concurrent_sessions=peak, mean_concurrent_sessions=area / pool.duration)
        manifest = dict(schema_version=3, generator="tracegen-config-v3", config=config,
                        timestamp_unit="seconds", window="[0, duration)",
                        timing="independent client renewal clocks; direct inter-request arrival gaps",
                        seed_scheme="sha256 UTF-8 compact JSON array, first 128 bits; session-v1 and named substreams",
                        rate_approximation="midpoint constant rates on traffic.resolution grid plus curve/burst knots",
                        concurrency_semantics="[first arrival, planned last arrival), clipped to duration; no service time",
                        hash_scheme="128-bit synthetic prefix chain; complete blocks only; private suffix isolated by session",
                        boundary="empty at zero; arrivals at/after duration omitted; empty hash_ids retained",
                        output_sha256_uncompressed=fingerprint.hexdigest(), stats=stats,
                        task_mix=[dict(key=k, **v) for k, v in sorted(task_stats.items())],
                        session_rate_segments=pool.rate_segments,
                        clients=[dict(task=c.task["key"], key=c.key, arrival=c.arrival,
                                      session_rate_segments=c.segments) for c in
                                 sorted(pool.clients, key=lambda c: (c.task["key"], c.key))],
                        sessions=session_list, session_concurrency=concurrency)
        fd, manifest_temp = tempfile.mkstemp(prefix=".tracegen-", dir=output.parent)
        temporary.append(manifest_temp)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(trace_temp, output)
        os.replace(manifest_temp, manifest_path)
        return manifest
    finally:
        for path in temporary:
            if os.path.exists(path):
                os.unlink(path)
