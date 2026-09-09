"""SwissAI exact-length buckets -> observed sessions or explicit request windows."""

from datetime import datetime, timezone
from contextlib import closing
import json
import math
import sqlite3
import tempfile

from ..sources import integer, positive
from .common import block_size_option, prefix_hashes


def epoch(value):
    if not isinstance(value, str):
        raise ValueError('SwissAI created_at must be an ISO datetime string')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class SwissAIFrontend:
    def configure(self, parser):
        block_size_option(parser)
        parser.add_argument('--session-mode', choices=('window','field'), required=True)
        parser.add_argument('--window-seconds', type=float, default=300)
        parser.add_argument('--session-field', default='session_id')
        parser.add_argument('--bucket-size', type=int, default=16,
                            help='16 for the documented qwen3-32b-buckets release')
        parser.add_argument('--token-count-field', default='token_count')
        parser.add_argument('--model', help='select a model; also supplies model namespace if field is absent')

    def convert(self, records, options, context):
        size = integer(options.bucket_size, 'bucket_size', 1)
        target_size = integer(options.block_size, 'block_size', 1)
        if target_size % size:
            raise ValueError('block_size must be a multiple of bucket_size')
        width = positive(options.window_seconds,'window_seconds')
        kind = 'request_window' if options.session_mode == 'window' else 'session'
        context.details.update(session_kind=kind, timing='observed_relative',
                               naive_datetime_timezone='UTC', bucket_size=size,
                               hash_namespace='input fingerprint + model; no identity sharing across files',
                               recommended_hash_id_scope='global', recommended_new_block_jitter=0)
        context.stats.update(filtered_records=0, dropped_padding_buckets=0)
        # Disk sorting accepts non-monotonic input without holding an entire trace in RAM.
        with tempfile.TemporaryDirectory(prefix='tracegen-swissai-') as folder:
            with closing(sqlite3.connect(folder+'/rows.sqlite')) as db:
                db.execute('CREATE TABLE rows (namespace TEXT, unit TEXT, t REAL, seq INTEGER, payload TEXT)')
                sequence = 0
                for row in records:
                    raw = row.data
                    model = raw.get('model',options.model)
                    if options.model and model != options.model:
                        context.stats['filtered_records'] += 1
                        continue
                    if not isinstance(model,str) or not model:
                        raise ValueError('SwissAI needs a model field or explicit --model')
                    if options.token_count_field not in raw:
                        raise ValueError('SwissAI record has no exact token count; bucket-reuse-only files '
                                         'cannot determine the partial tail. Use qwen3-32b-buckets.jsonl '
                                         'or join exact token counts before conversion')
                    n = integer(raw[options.token_count_field],options.token_count_field)
                    buckets = raw.get('bucket_ids')
                    if not isinstance(buckets,list) or any(type(v) is not int or v < 0 for v in buckets):
                        raise ValueError('SwissAI bucket_ids must be a list of nonnegative integers')
                    if len(buckets) not in (n//size, (n+size-1)//size):
                        raise ValueError('SwissAI bucket count disagrees with exact token count/bucket size')
                    if 'total_buckets' in raw and raw['total_buckets'] != len(buckets):
                        raise ValueError('SwissAI total_buckets disagrees with bucket_ids')
                    timestamp = epoch(raw['created_at'])
                    if not math.isfinite(timestamp):
                        raise ValueError('SwissAI non-finite timestamp')
                    if options.session_mode == 'window':
                        unit = str(math.floor(timestamp/width))
                    else:
                        unit = raw.get(options.session_field)
                        if not isinstance(unit,(str,int)) or isinstance(unit,bool) or unit == '':
                            raise ValueError(f'SwissAI missing session field {options.session_field!r}')
                        unit = str(unit)
                    namespace = context.sources[row.file_index]['sha256']+':'+model
                    context.stats['dropped_padding_buckets'] += len(buckets)-n//size
                    request = dict(timestamp=timestamp, hash_ids=prefix_hashes(
                        buckets[:n//size], target_size // size, namespace))
                    db.execute('INSERT INTO rows VALUES (?,?,?,?,?)',
                               (namespace,unit,timestamp,sequence,json.dumps(request,separators=(',',':'))))
                    sequence += 1
                db.commit()
                previous = None
                requests = []

                def session(items):
                    origin = items[0]['timestamp']
                    for req in items:
                        req['timestamp'] -= origin
                    return dict(requests=items)

                for namespace,unit,_,_,payload in db.execute('SELECT * FROM rows ORDER BY namespace,unit,t,seq'):
                    key = (namespace,unit)
                    if previous is not None and key != previous:
                        yield session(requests)
                        requests = []
                    requests.append(json.loads(payload))
                    previous = key
                if previous is not None:
                    yield session(requests)
