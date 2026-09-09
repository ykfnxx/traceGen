"""Minimal backend input: one {requests: [{timestamp, hash_ids}]} per line."""

import math


def validate_session(record):
    if not isinstance(record, dict) or set(record) != {"requests"}:
        raise ValueError("session must contain only requests; convert raw/legacy data with prepare.py")
    requests = record["requests"]
    if not isinstance(requests, list) or not requests:
        raise ValueError("session must contain at least one model request")
    for req in requests:
        if not isinstance(req, dict) or set(req) != {"timestamp", "hash_ids"}:
            raise ValueError("request must contain only timestamp and hash_ids")
        t = req["timestamp"]
        if (isinstance(t, bool) or not isinstance(t, (int, float))
                or not math.isfinite(t) or t < 0):
            raise ValueError("request timestamp must be finite and >= 0")
        values = req["hash_ids"]
        if not isinstance(values, list):
            raise ValueError("hash_ids must be a list")
        if any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in values):
            raise ValueError("hash_ids must contain integer or string identities")
    return record
