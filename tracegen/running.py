"""按单请求 decode 速度估算并发；纯统计，不改变请求时序。"""

from collections import defaultdict
import math


def estimate_running(requests, duration, window, next_arrivals=None):
    """requests 为 (timestamp, session_id, output_tokens)；按墙钟时间加权。"""
    scenarios = []
    for speed in (50, 80, 100):
        events = defaultdict(int, {0.0: 0, duration: 0})
        last = {}
        conflicts = pairs = beyond = 0
        for index, (start, sid, output) in enumerate(requests):
            end = start + output / speed
            events[start] += 1
            events[min(end, duration)] -= 1
            beyond += end > duration
            if next_arrivals is not None:
                pairs += 1
                conflicts += end > next_arrivals[index]
            elif sid in last:
                pairs += 1
                conflicts += last[sid] > start
            last[sid] = end
        histogram = defaultdict(float)
        areas = [0.0] * math.ceil(duration / window)
        active, previous = 0, 0.0
        for timestamp, delta in sorted(events.items()):
            if timestamp > previous:
                histogram[active] += timestamp - previous
                cursor = previous
                while cursor < timestamp:
                    index = min(int(cursor / window), len(areas) - 1)
                    stop = min(timestamp, (index + 1) * window)
                    areas[index] += active * (stop - cursor)
                    cursor = stop
            active += delta
            previous = timestamp
        pmf = [[n, histogram[n] / duration] for n in sorted(histogram)]
        cumulative, cdf = 0.0, []
        for n, probability in pmf:
            cumulative += probability
            cdf.append([n, cumulative])
        scenarios.append(dict(tokens_per_second_per_request=speed,
            mean=sum(n * p for n, p in pmf),
            **{f'p{q}': next(n for n, p in cdf if p >= q / 100) for q in (50, 95, 99)},
            peak=max(histogram), idle_fraction=histogram.get(0, 0) / duration,
            conflict_count=conflicts, adjacent_pairs=pairs,
            conflict_fraction=conflicts / pairs if pairs else None,
            still_running_at_end=beyond, pmf=pmf, cdf=cdf,
            window_means=[area / min(window, duration - i * window)
                          for i, area in enumerate(areas)]))
    return dict(semantics='decode-only estimate: immediate start, fixed per-request speed; '
                'no prefill, queue, preemption or concurrency slowdown; '
                'wall-time weighted on [0, duration); conflicts retained, not serving measurements',
                scenarios=scenarios)
