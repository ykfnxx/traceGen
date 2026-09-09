"""Vary new suffix lengths while preserving the reference prefix-tree branches."""

from array import array
import math
import random

from .sources import digest


def validate_jitter(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError("new_block_jitter must be a finite number in [0, 1]")
    return value


def prefix_segments(requests):
    """Compress paths, retaining every request endpoint and branching point.

    Inspect the full template, including future requests: otherwise shortening
    today's suffix could erase a prefix that a later request needs to reuse.
    Return endpoint -> (parent endpoint or None, original block count).
    """
    parents = {}
    first_child = {}
    boundaries = set()
    for request in requests:
        if request.hashes:
            boundaries.add(request.hashes[-1])
        parent = None
        for value in request.hashes:
            if value not in parents:
                parents[value] = parent
                if parent in first_child:
                    if parent is not None:
                        boundaries.add(parent)
                else:
                    first_child[parent] = value
            elif parents[value] != parent:
                raise ValueError("reference hash has inconsistent prefix parent")
            parent = value
    segments = {}
    for end in boundaries:
        size = 1
        parent = parents[end]
        while parent is not None and parent not in boundaries:
            size += 1
            parent = parents[parent]
        segments[end] = (parent, size)
    return segments


class BlockVariation:
    def __init__(self, segments, session_id, seed, jitter):
        self.segments = segments
        self.session_id = session_id
        self.jitter = validate_jitter(jitter)
        # Independent from arrivals, template choice, and other session streams.
        self.rng = random.Random(f"new-blocks-v1:{seed}:{session_id}")
        self.generated = {}
        self.stats = dict(reference_new_blocks=0, synthetic_new_blocks=0,
                          requests_with_changed_new_blocks=0)

    def materialize(self, values):
        if not values:
            return []
        path = []
        endpoint = values[-1]
        while endpoint is not None:
            path.append(endpoint)
            endpoint = self.segments[endpoint][0]
        path.reverse()
        fresh = [end for end in path if end not in self.generated]
        original = generated = 0
        if fresh:
            factor = self.rng.uniform(1 - self.jitter, 1 + self.jitter)
            for end in fresh:
                parent, size = self.segments[end]
                scaled = size * factor
                base = math.floor(scaled)
                count = max(1, base + (self.rng.random() < scaled - base))
                hashes = array("Q")
                previous = (self.generated[parent][-1] if parent is not None else
                            digest(["tracegen-varied-root-v1", self.session_id]))
                for index in range(count):
                    previous = digest(["tracegen-varied-block-v1", previous, end, index])
                    hashes.append(previous)
                self.generated[end] = hashes
                original += size
                generated += count
        self.stats["reference_new_blocks"] += original
        self.stats["synthetic_new_blocks"] += generated
        self.stats["requests_with_changed_new_blocks"] += original != generated
        return [value for end in path for value in self.generated[end]]
