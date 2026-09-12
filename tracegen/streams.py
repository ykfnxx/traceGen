"""Independent source offer streams, merged before global FIFO admission."""
import heapq
import math
import random

from .traffic import offer_times, rate_segments


def independent(config):
    return any('traffic' in s for s in config['datasets'])


def source_segments(config, spec, scale=1.0):
    segments = [dict(s, session_rate=s['session_rate'] * scale)
                for s in rate_segments(dict(spec['traffic'], duration=config['duration']))]
    if any(not math.isfinite((s['end']-s['start'])*s['session_rate']) for s in segments):
        raise ValueError('scaled session traffic overflow')
    return segments


def rate_scales(config, datasets):
    return ([s.get('rate_scale', 1.0) for s in config['datasets']]
            if independent(config) else [d.weight for d in datasets])


class SessionOffers:
    """Each independent offer fixes its source/template before waiting for a slot.

    Legacy input keeps its historical admission-time sampling and random streams.
    Source names break simultaneous-offer ties and seed independent RNGs.
    """
    def __init__(self, config, datasets, scales):
        self.datasets = datasets
        self.scales = scales
        self.independent = independent(config)
        seed = config.get('seed', 0)
        if self.independent:
            self.segments = {s['name']: source_segments(config, s, w)
                             for s, w in zip(config['datasets'], scales)}
            self.choosers = [random.Random(f'template:{seed}:{d.name}') for d in datasets]
            def stream(i, spec):
                clock = offer_times(random.Random(f'arrival:{seed}:{spec["name"]}'),
                                    self.segments[spec['name']], spec['traffic'].get('arrival', {}))
                for ordinal, timestamp in enumerate(clock):
                    yield timestamp, spec['name'], i, ordinal
            self.offers = heapq.merge(*(stream(i, s) for i, s in enumerate(config['datasets'])))
        else:
            self.segments = rate_segments(config)
            self.chooser = random.Random(f'template:{seed}')
            self.offers = ((t, '', None, None) for t in offer_times(
                random.Random(f'arrival:{seed}'), self.segments, config.get('arrival', {})))

    def next(self):
        timestamp, _, i, ordinal = next(self.offers, (math.inf, '', None, None))
        index = self.choosers[i].randrange(len(self.datasets[i].offsets)) if i is not None else None
        return timestamp, i, index, ordinal

    def admit(self, event):
        _, i, index, ordinal = event
        if i is None:
            d = self.chooser.choices(self.datasets, weights=self.scales, k=1)[0]
            return d, self.chooser.randrange(len(d.offsets)), None
        d = self.datasets[i]
        return d, index, f'{d.name}:{ordinal:08d}'
