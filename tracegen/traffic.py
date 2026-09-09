"""Session offer traffic in physical seconds; no scaling of request timelines."""

import math

from .sources import positive


def nonnegative(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be a finite nonnegative number")
    return value


def arrival_intervals(rng, rate, arrival):
    positive(rate, "arrival rate")
    distribution = arrival.get("distribution", "gamma")
    if distribution == "gamma":
        cv = nonnegative(arrival.get("cv", 1.0), "arrival.cv")
        if cv == 0:
            sample = lambda: 1.0 / rate
        else:
            variance = cv * cv
            if not math.isfinite(variance) or variance == 0:
                raise ValueError("arrival.cv is numerically out of range")
            shape, scale = 1.0 / variance, variance / rate
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


def rate_segments(config):
    """Nonoverlapping burst windows override the absolute base session rate."""
    duration = positive(config.get("duration"), "duration")
    base = nonnegative(config.get("session_rate"), "session_rate")
    bursts = config.get("bursts", [])
    if not isinstance(bursts, list):
        raise ValueError("bursts must be a list")
    windows = []
    for burst in bursts:
        if not isinstance(burst, dict):
            raise ValueError("each burst must be an object")
        start = nonnegative(burst.get("start"), "burst.start")
        end = start + positive(burst.get("duration"), "burst.duration")
        rate = nonnegative(burst.get("session_rate"), "burst.session_rate")
        if start >= duration or end > duration:
            raise ValueError("burst window must be inside [0, duration]")
        windows.append((start, end, rate))
    segments, cursor = [], 0.0
    for start, end, rate in sorted(windows):
        if start < cursor:
            raise ValueError("burst windows must not overlap")
        if start > cursor:
            segments.append({"start": cursor, "end": start, "session_rate": base})
        segments.append({"start": start, "end": end, "session_rate": rate})
        cursor = end
    if cursor < duration:
        segments.append({"start": cursor, "end": duration, "session_rate": base})
    if any(not math.isfinite((s["end"] - s["start"]) * s["session_rate"]) for s in segments):
        raise ValueError("integrated session rate is numerically out of range")
    return segments


def offer_times(rng, segments, arrival):
    """Invert integrated rate, preserving renewal residual across rate changes.

    At constant rate this is Gamma/Weibull IAT sampling. CV=1 Gamma gives
    a nonhomogeneous Poisson process; CV=0 gives evenly spaced rate-integral
    increments. A rate change does not reset the random arrival clock.
    """
    intervals = arrival_intervals(rng, 1.0, arrival)
    remaining = next(intervals)
    horizon = segments[-1]["end"]
    previous = -1.0
    for segment in segments:
        position, end, rate = segment["start"], segment["end"], segment["session_rate"]
        if rate == 0:
            continue
        while position < end:
            capacity = (end - position) * rate
            if remaining > capacity:
                remaining -= capacity
                break
            timestamp = min(end, position + remaining / rate)
            if timestamp <= previous:
                raise ValueError("session offer clock lost precision")
            if timestamp >= horizon:
                return
            yield timestamp
            previous = position = timestamp
            remaining = next(intervals)
