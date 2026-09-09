"""Shared streaming readers and conversion context; no dataset-specific rules."""

from dataclasses import dataclass, field
import gzip
import hashlib
import json
from pathlib import Path


@dataclass
class RawRecord:
    data: dict
    file_index: int
    row_index: int


@dataclass
class ConversionContext:
    sources: list
    stats: dict = field(default_factory=lambda: {"raw_records": 0})
    details: dict = field(default_factory=dict)


def fingerprint(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def records(paths, context, limit=None):
    for file_index, path in enumerate(paths):
        path = Path(path)
        if path.suffix == '.parquet':
            try:
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise ValueError('Parquet input requires pip install -r requirements-frontends.txt') from exc
            def parquet_rows():
                for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
                    yield from batch.to_pylist()
            iterator = parquet_rows()
        else:
            def json_rows():
                opener = gzip.open if path.suffix == '.gz' else open
                with opener(path, 'rt', encoding='utf-8') as stream:
                    for number, line in enumerate(stream, 1):
                        if not line.strip():
                            continue
                        if line.startswith('version https://git-lfs.github.com/spec/'):
                            raise ValueError(f'{path} is a Git LFS pointer; download its actual content')
                        try:
                            yield json.loads(line)
                        except ValueError as exc:
                            raise ValueError(f'{path}:{number}: invalid JSONL') from exc
            iterator = json_rows()
        try:
            for index, data in enumerate(iterator):
                if not isinstance(data, dict):
                    raise ValueError(f'{path}, row {index+1}: expected an object')
                context.stats['raw_records'] += 1
                yield RawRecord(data, file_index, index)
                if limit is not None and context.stats['raw_records'] >= limit:
                    return
        finally:
            iterator.close()


def prefix_hashes(values, width, namespace=None):
    """Hash complete ordered groups, chaining the entire preceding prefix."""
    from ..sources import digest, integer
    integer(width, 'block width', 1)
    if not isinstance(values, list) or any(isinstance(v, bool) or not isinstance(v, (int, str)) for v in values):
        raise ValueError('block identities must be a list of integers or strings')
    parent = digest(['tracegen-frontend-prefix-v1', namespace])
    result = []
    for start in range(0, len(values) // width * width, width):
        parent = digest([parent, values[start:start + width]])
        result.append(parent)
    return result


def block_size_option(parser):
    parser.add_argument('--block-size', type=int, default=128,
                        help='normalized KV block size in tokens, default 128; must match generation')
