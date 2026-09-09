"""Read complete session templates without retaining the whole source corpus."""

from array import array
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path


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
    num_tokens: int


@dataclass(frozen=True)
class SessionTemplate:
    source_id: str
    requests: tuple[RequestTemplate, ...]
    scope: str


def leaves(requests):
    """Weka nested request t values already use the outer session's clock."""
    if not isinstance(requests, list):
        raise ValueError("requests must be a list")
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("each request must be an object")
        if "requests" in request:
            if "hash_ids" in request or "token_ids" in request:
                raise ValueError("a request group cannot also contain request content")
            yield from leaves(request["requests"])
        else:
            yield request


def compile_session(record, block_size, source_name, row_number, source_format):
    """Hash ordered groups, chaining each digest to its full preceding prefix.

    Source hash labels are opaque prefix identities, not recoverable token data.
    Hash-only inputs therefore support coarsening by integer multiples only.
    """
    integer(block_size, "block_size", 1)
    if not isinstance(record, dict):
        raise ValueError("each JSONL record must be a session object")
    scope = record.get("hash_id_scope", "local")
    if scope not in ("local", "global"):
        raise ValueError("hash_id_scope must be local or global")
    source_id = str(record.get("id", row_number))
    root = digest(["tracegen-prefix-v1", source_name,
                   row_number if scope == "local" else "global", block_size])

    # Cache repeated prefix edges within a template; bound temporary memory.
    @lru_cache(maxsize=65536)
    def edge(parent, kind, values):
        return digest([parent, kind, values])

    result = []
    for request in leaves(record.get("requests")):
        time_key = "t" if source_format == "weka" else "timestamp"
        offset = request.get(time_key)
        if (isinstance(offset, bool) or not isinstance(offset, (int, float))
                or not math.isfinite(offset) or offset < 0):
            raise ValueError(f"request {time_key} must be finite and >= 0")
        if "token_ids" in request:
            values = request["token_ids"]
            if not isinstance(values, list):
                raise ValueError("token_ids must be a list")
            for token in values:
                integer(token, "token_id")
            n_tokens = request.get("num_tokens", len(values))
            integer(n_tokens, "num_tokens")
            if n_tokens != len(values):
                raise ValueError("num_tokens must equal len(token_ids)")
            width, kind = block_size, "tokens"
        else:
            source_block_size = integer(record.get("block_size"), "source block_size", 1)
            if block_size % source_block_size:
                raise ValueError(
                    f"block_size={block_size} must be a multiple of source "
                    f"block_size={source_block_size}; finer content is unavailable")
            values = request.get("hash_ids")
            if not isinstance(values, list):
                raise ValueError("hash_ids must be a list")
            if any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in values):
                raise ValueError("source hash_ids must be strings or integers")
            n_tokens = request.get("in" if source_format == "weka" else "num_tokens")
            integer(n_tokens, "request token count")
            if len(values) != n_tokens // source_block_size:
                raise ValueError("hash_ids must cover exactly all complete source blocks")
            width, kind = block_size // source_block_size, "source-prefix-hashes"
        hashes = array("Q")
        parent = root
        for start in range(0, (len(values) // width) * width, width):
            parent = edge(parent, kind, tuple(values[start:start + width]))
            hashes.append(parent)
        result.append(RequestTemplate(float(offset), hashes, n_tokens))
    if not result:
        raise ValueError("session must contain at least one model request")
    result.sort(key=lambda request: request.offset)  # Stable for simultaneous requests.
    origin = result[0].offset
    result = tuple(RequestTemplate(r.offset - origin, r.hashes, r.num_tokens) for r in result)
    return SessionTemplate(source_id, result, scope)


class Dataset:
    """Index JSONL byte offsets once; parse/compile only selected sessions."""

    def __init__(self, spec, block_size):
        self.name = spec["name"]
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("dataset name must be a nonempty string")
        self.path = Path(spec["path"]).resolve()
        self.format = spec.get("format", "session_jsonl")
        if self.format not in ("weka", "session_jsonl"):
            raise ValueError(f"unknown dataset format: {self.format}")
        self.weight = positive(spec.get("weight", 1), "dataset weight")
        self.block_size = block_size
        self.offsets = []
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
                    self.offsets.append(offset)
        if not self.offsets:
            raise ValueError(f"empty dataset: {self.path}")
        self.sha256 = fingerprint.hexdigest()

    def get(self, index):
        if index in self.cache:
            self.cache.move_to_end(index)
            return self.cache[index]
        try:
            with self.path.open("rb") as stream:
                stream.seek(self.offsets[index])
                record = json.loads(stream.readline())
            template = compile_session(record, self.block_size, self.name, index, self.format)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{self.path}, session record {index + 1}: {exc}") from exc
        self.cache[index] = template
        if len(self.cache) > 4:
            self.cache.popitem(last=False)
        return template
