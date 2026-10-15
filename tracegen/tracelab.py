"""Read and screen whole TraceLab sessions; timestamps are input-ready proxies."""
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path


def timestamp(value):
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value.timestamp() if value.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class TraceLab:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        with open(path, 'rb') as stream:
            self.sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        grouped = defaultdict(list)
        opener = gzip.open if str(path).endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                provider, sid = row.get('provider'), row.get('session_id')
                if not isinstance(provider, str) or not isinstance(sid, str):
                    raise ValueError('TraceLab rows require provider and session_id')
                flags = set()
                inputs, outputs, users = [], [], 0
                for event in row.get('timing_events') or []:
                    kind = event.get('event_type')
                    users += kind == 'user_message'
                    if kind in ('user_message', 'tool_result', 'reasoning', 'text', 'tool_call'):
                        t = timestamp(event.get('timestamp'))
                        if t is None:
                            flags.add('invalid_timestamp')
                        else:
                            (inputs if kind in ('user_message', 'tool_result') else outputs).append(t)
                candidates = [t for t in inputs if outputs and t <= min(outputs)]
                anchor = max(candidates) if candidates else None
                if anchor is None:
                    flags.add('missing_anchor')
                count = row.get('current_user_message_count')
                if not integer(count) or count != users:
                    flags.add('invalid_user_count')
                if not all(integer(row.get(k)) for k in ('round_index', 'input_tokens_total', 'output_tokens')):
                    flags.add('invalid_numbers')
                grouped[provider, sid].append(dict(index=row.get('round_index'), anchor=anchor,
                    input_tokens=row.get('input_tokens_total'), output_tokens=row.get('output_tokens'),
                    current_user_message_count=count, flags=flags, round_id=row.get('round_id')))
        self.templates = []
        self.excluded = Counter()
        self.raw_requests = sum(map(len, grouped.values()))
        self.raw_sessions = len(grouped)
        for (provider, sid), rows in sorted(grouped.items()):
            flags = set().union(*(r['flags'] for r in rows))
            if 'invalid_numbers' not in flags:
                rows.sort(key=lambda r: r['index'])
                if len({r['index'] for r in rows}) != len(rows):
                    flags.add('duplicate_round_index')
            ids = [r['round_id'] for r in rows if r['round_id'] is not None]
            if len(ids) != len(set(ids)):
                flags.add('duplicate_round_id')
            if all(r['anchor'] is not None for r in rows):
                if any(b['anchor'] < a['anchor'] for a, b in zip(rows, rows[1:])):
                    flags.add('backward_anchor')
            if flags:
                self.excluded.update(flags)
                continue
            records = [dict(relative_time=r['anchor']-rows[0]['anchor'], request_index=i,
                input_tokens=r['input_tokens'], output_tokens=r['output_tokens'], external_tokens=None,
                current_user_message_count=r['current_user_message_count'],
                is_agent_turn_end=rows[i+1]['current_user_message_count'] > 0 if i+1 < len(rows) else None)
                for i, r in enumerate(rows)]
            self.templates.append(dict(provider=provider, session_id=sid, records=records))

    def select(self, limit):
        templates = [t for t in self.templates if limit is None or
                     all(r['input_tokens'] <= limit for r in t['records'])]
        if not templates:
            raise ValueError(f'TraceLab: no eligible sessions in {self.path}')
        return templates, dict(path=self.path, sha256=self.sha256, raw_sessions=self.raw_sessions,
            raw_requests=self.raw_requests, valid_sessions=len(self.templates),
            excluded_session_reasons=dict(self.excluded), excluded_context_sessions=len(self.templates)-len(templates),
            eligible_sessions=len(templates), eligible_requests=sum(len(t['records']) for t in templates),
            timing='last input at/before first model output; relative to first request; nondecreasing',
            sampling='uniform whole sessions with replacement', max_context_tokens=limit)
