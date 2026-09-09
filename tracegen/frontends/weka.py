"""Weka nested model calls -> minimal relative-time session JSONL."""

from .common import block_size_option, prefix_hashes
from ..schema import validate_session
from ..sources import integer


def normalize_weka(record, block_size=128):
    size = integer(record['block_size'], 'source block_size', 1)
    integer(block_size, 'block_size', 1)
    if block_size % size:
        raise ValueError('block_size must be a multiple of source block_size')
    requests = []

    def visit(items):
        if not isinstance(items, list):
            raise ValueError('Weka requests must be a list')
        for req in items:
            if not isinstance(req, dict):
                raise ValueError('Weka request must be an object')
            if 'requests' in req:
                if 'hash_ids' in req or 'token_ids' in req:
                    raise ValueError('request group cannot also contain content')
                visit(req['requests'])
            else:
                n = integer(req['in'], 'request token count')
                values = req['hash_ids']
                if not isinstance(values, list) or len(values) != n // size:
                    raise ValueError('hash_ids must cover exactly all complete source blocks')
                requests.append(dict(timestamp=req['t'], hash_ids=prefix_hashes(values, block_size // size)))
    visit(record.get('requests'))
    result = validate_session(dict(requests=requests))
    # Inner t is already relative to the OUTER session, never add container t.
    requests.sort(key=lambda req: req['timestamp'])
    origin = requests[0]['timestamp']
    for req in requests:
        req['timestamp'] -= origin
    return result


class WekaFrontend:
    def configure(self, parser):
        block_size_option(parser)

    def convert(self, records, options, context):
        context.details.update(timing='observed_relative', session_kind='session')
        for row in records:
            yield normalize_weka(row.data, options.block_size)


def prepare_weka(source, output, block_size=128):
    """Convenience for Weka experiments; no raw format enters the backend."""
    from argparse import Namespace
    from prepare import prepare
    return prepare(WekaFrontend(), Namespace(frontend='weka', input=[source], output=output,
                                             limit=None, block_size=block_size))
