"""Calibrate session sampling weights to final, nonempty request proportions.

The lightweight count replay models admission and the finite output window;
it does not generate hashes or drop valid requests to enforce a quota.
"""
from collections import deque
import heapq
import math
import random

from .generator import validate_config
from .sources import positive, integer
from .traffic import offer_times, rate_segments


def count_replay(config, datasets, weights):
    """Predict the generator's source choices, starts and emitted request counts."""
    validate_config(config)
    if len(weights) != len(datasets):
        raise ValueError('one weight required per dataset')
    for w in weights:
        positive(w, 'sampling weight')
    duration = config['duration']
    seed = config.get('seed', 0)
    offers = offer_times(random.Random(f'arrival:{seed}'), rate_segments(config),
                         config.get('arrival', {'distribution':'gamma', 'cv':1.0}))
    next_offer = next(offers, math.inf)
    chooser = random.Random(f'template:{seed}')
    releases, waiting, sessions = [], deque(), []
    active = peak = 0
    counts = dict.fromkeys((d.name for d in datasets), 0)
    while next_offer < duration or releases:
        if releases and releases[0][0] <= next_offer:
            timestamp, _ = heapq.heappop(releases)
            active -= 1
        else:
            timestamp = next_offer
            waiting.append(timestamp)
            next_offer = next(offers, math.inf)
        while waiting and active < config['max_concurrent_sessions']:
            offered = waiting.popleft()
            d = chooser.choices(datasets, weights=weights, k=1)[0]
            index = chooser.randrange(len(d.offsets))
            times = d.timelines[index]
            count = sum(timestamp + offset < duration for offset in times)
            end = timestamp + times[-1]
            if not math.isfinite(end):
                raise ValueError('session timeline overflow')
            sessions.append(dict(source=d.name, source_record=d.record_numbers[index], start=timestamp,
                                 offered_start=offered, end=end, emitted_requests=count,
                                 template_requests=len(times)))
            counts[d.name] += count
            active += 1
            peak = max(peak, active)
            if end < duration:
                heapq.heappush(releases, (end, len(sessions)-1))
    return dict(request_counts=counts, requests=sum(counts.values()), sessions=sessions,
                peak_concurrent_sessions=peak, pending_sessions_at_end=len(waiting))


def calibrate(config, datasets, targets, tolerance=.01, max_iterations=100):
    """Find weights within absolute request-share tolerance for this exact seed/window.

    Whole-session stochastic sampling is discrete. Report failure explicitly if
    the requested tolerance cannot be met; do not silently claim exact ratios.
    """
    if len(targets) != len(datasets):
        raise ValueError('one target request ratio required per dataset')
    total = sum(positive(v, 'target request ratio') for v in targets)
    targets = [v / total for v in targets]
    positive(tolerance, 'tolerance')
    if tolerance >= 1:
        raise ValueError('tolerance must be less than 1')
    integer(max_iterations, 'max_iterations', 1)
    means = [sum(map(len, d.timelines))/len(d.timelines) for d in datasets]
    weights = [target / mean for target, mean in zip(targets, means)]
    norm = sum(weights)
    weights = [w/norm for w in weights]
    rng = random.Random(f'mix-calibration:{config.get("seed",0)}')
    best = None
    history = []
    for iteration in range(max_iterations):
        prediction = count_replay(config, datasets, weights)
        if prediction['requests'] == 0:
            raise ValueError('no nonempty requests arrive in the configured window')
        shares = [prediction['request_counts'][d.name]/prediction['requests'] for d in datasets]
        error = max(abs(a-b) for a,b in zip(shares,targets))
        history.append(dict(iteration=iteration, weights=list(weights), actual_request_shares=shares,
                            requests=prediction['requests'], max_absolute_error=error))
        if best is None or error < best['max_absolute_error']:
            best = dict(weights=list(weights), actual_request_shares=shares, max_absolute_error=error,
                        prediction=prediction)
        if error <= tolerance:
            return dict(**best, target_request_shares=targets, tolerance=tolerance,
                        iterations=iteration+1, history=history)
        # Multiplicative correction accounts for admission and cutoff, with
        # damped updates and deterministic local exploration to escape steps.
        if iteration % 4 == 3:
            radius = max(.025, min(.6, best['max_absolute_error']*2))
            weights = [w*math.exp(rng.gauss(0,radius)) for w in best['weights']]
        else:
            weights = [w*(target/max(share,.5/prediction['requests']))**.6
                       for w,target,share in zip(weights,targets,shares)]
        norm = sum(weights)
        weights = [w/norm for w in weights]
    raise ValueError(f'request mix did not converge within {max_iterations} iterations: '
                     f'best error {best["max_absolute_error"]:.4%}, tolerance {tolerance:.4%}; '
                     'increase session_rate/duration or relax tolerance')
