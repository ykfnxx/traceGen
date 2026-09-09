"""Read complete session templates without retaining the whole source corpus."""

from array import array
from collections import OrderedDict
from dataclasses import dataclass
from functools import cached_property, lru_cache
import hashlib
import json
import math
from pathlib import Path

from .schema import validate_session


def positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return int.from_bytes(hashlib.blake2b(encoded, digest_size=8).digest(), "big")


@dataclass(frozen=True)
class RequestTemplate:
    offset: float
    hashes: array


@dataclass(frozen=True)
class SessionTemplate:
    source_id: str
    requests: tuple[RequestTemplate, ...]
    scope: str

    @cached_property
    def prefix_segments(self):
        from .variation import prefix_segments
        return prefix_segments(self.requests)


def compile_session(record, block_size, source_name, row_number, source_format="session_jsonl",
                    *, scope="local"):
    """Compile complete block identities; tokenization/reblocking belong to frontends."""
    integer(block_size, "block_size", 1)
    if source_format != "session_jsonl":
        raise ValueError("backend accepts only session_jsonl; convert raw data with prepare.py")
    validate_session(record)
    if scope not in ("local", "global"):
        raise ValueError("dataset hash_id_scope must be local or global")
    root = digest(["tracegen-prefix-v2", source_name,
                   row_number if scope == "local" else "global", block_size])

    @lru_cache(maxsize=65536)
    def edge(parent, value):
        return digest([parent, value])

    result = []
    for request in record["requests"]:
        hashes = array("Q")
        parent = root
        for value in request["hash_ids"]:
            parent = edge(parent, value)
            hashes.append(parent)
        result.append(RequestTemplate(float(request["timestamp"]), hashes))
    result.sort(key=lambda request: request.offset)
    origin = result[0].offset
    return SessionTemplate(str(row_number), tuple(
        RequestTemplate(r.offset - origin, r.hashes) for r in result), scope)


class Dataset:
    """Index JSONL byte offsets once; parse/compile only selected sessions."""

    def __init__(self, spec, block_size):
        self.name = spec["name"]
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("dataset name must be a nonempty string")
        self.path = Path(spec["path"]).resolve()
        self.format = spec.get("format", "session_jsonl")
        if self.format != "session_jsonl":
            raise ValueError("backend accepts only session_jsonl; convert raw data with prepare.py")
        from .variation import validate_jitter
        self.new_block_jitter = spec.get("new_block_jitter")
        if "new_block_jitter" in spec:
            validate_jitter(self.new_block_jitter)
        self.weight = positive(spec.get("weight", 1), "dataset weight")
        self.scope = spec.get("hash_id_scope", "local")
        if self.scope not in ("local", "global"):
            raise ValueError("dataset hash_id_scope must be local or global")
        self.block_size = integer(block_size, "block_size", 1)
        declared_size = integer(spec.get("block_size", block_size), "dataset block_size", 1)
        if declared_size != block_size:
            raise ValueError("dataset block_size must match generation block_size; rerun prepare.py")
        self.offsets = []
        self.record_numbers = []
        self.timelines = []
        self.filter_stats = dict(input_sessions=0, input_requests=0,
                                 excluded_empty_requests=0, excluded_empty_sessions=0)
        self.cache = OrderedDict()
        fingerprint = hashlib.sha256()
        with self.path.open("rb") as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                fingerprint.update(line)
                if line.strip():
                    row_number = self.filter_stats['input_sessions']
                    try:
                        record = validate_session(json.loads(line))
                    except (ValueError, TypeError, KeyError) as exc:
                        raise ValueError(f'{self.path}, session record {row_number+1}: {exc}') from exc
                    self.filter_stats['input_sessions'] += 1
                    self.filter_stats['input_requests'] += len(record['requests'])
                    times = sorted(r['timestamp'] for r in record['requests'] if r['hash_ids'])
                    self.filter_stats['excluded_empty_requests'] += len(record['requests'])-len(times)
                    if not times:
                        self.filter_stats['excluded_empty_sessions'] += 1
                        continue
                    self.offsets.append(offset)
                    self.record_numbers.append(row_number)
                    self.timelines.append(array('d', (t-times[0] for t in times)))
        if not self.offsets:
            raise ValueError(f"dataset has no nonempty requests: {self.path}")
        self.sha256 = fingerprint.hexdigest()
        manifest_path = Path(str(self.path)+'.manifest.json')
        self.normalization = None
        if manifest_path.exists():
            self.normalization = json.loads(manifest_path.read_text())
            if self.normalization.get('output_sha256') != self.sha256:
                raise ValueError(f'{manifest_path}: normalization manifest does not match dataset SHA256')
            prepared_size = self.normalization.get('block_size')
            if prepared_size is not None and prepared_size != block_size:
                raise ValueError('prepared block_size must match generation block_size; rerun prepare.py')

    def get(self, index):
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.offsets[index])
                record = json.loads(stream.readline())
            record = dict(requests=[r for r in record['requests'] if r['hash_ids']])
            template = compile_session(record, self.block_size, self.name, self.record_numbers[index],
                                       self.format, scope=self.scope)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{self.path}, session record {index + 1}: {exc}") from exc
        self.cache[index] = template
        if len(self.cache) > 4:
            self.cache.popitem(last=False)
        return template
