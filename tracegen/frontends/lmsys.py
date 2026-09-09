"""LMSYS conversation text -> tokenized request contexts, with synthetic turn times."""

import hashlib
import math
import random

from ..sources import integer, positive
from .common import block_size_option, prefix_hashes


class HFChatTokenizer:
    def __init__(self, name, revision=None):
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise ValueError('LMSYS requires pip install -r requirements-frontends.txt') from exc
        self.tokenizer = AutoTokenizer.from_pretrained(name, revision=revision, trust_remote_code=False)
        template = self.tokenizer.get_chat_template()
        backend = self.tokenizer.backend_tokenizer.to_str()
        self.metadata = dict(name=name, revision=revision,
                             resolved_commit=self.tokenizer.init_kwargs.get('_commit_hash'),
                             tokenizer_sha256=hashlib.sha256(backend.encode()).hexdigest(),
                             chat_template_sha256=hashlib.sha256(template.encode()).hexdigest())

    def encode(self, messages):
        return self.tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                 return_dict=False)


class LMSYSFrontend:
    def __init__(self, tokenizer=None):
        self.tokenizer = tokenizer

    def configure(self, parser):
        block_size_option(parser)
        parser.add_argument('--tokenizer', required=True, help='HF tokenizer repo or local tokenizer directory')
        parser.add_argument('--revision', help='pinned tokenizer commit/revision')
        parser.add_argument('--turn-interval-mean', type=float, default=30)
        parser.add_argument('--turn-interval-cv', type=float, default=1)
        parser.add_argument('--seed', type=int, default=42)
        parser.add_argument('--model', help='select source model label; independent of target tokenizer')
        parser.add_argument('--language')

    def convert(self, records, options, context):
        size = integer(options.block_size, 'block_size', 1)
        mean = positive(options.turn_interval_mean,'turn_interval_mean')
        cv = options.turn_interval_cv
        if isinstance(cv,bool) or not isinstance(cv,(int,float)) or not math.isfinite(cv) or not 0 <= cv <= 100:
            raise ValueError('turn_interval_cv must be finite and in [0,100]')
        integer(options.seed,'seed')
        tokenizer = self.tokenizer or HFChatTokenizer(options.tokenizer,options.revision)
        context.details.update(timing='synthetic_gamma', turn_interval_mean=mean, turn_interval_cv=cv,
                               tokenizer=tokenizer.metadata, session_kind='conversation',
                               content='target chat template; prior assistant responses included, current response excluded')
        context.stats.update(filtered_records=0, conversations_without_responses=0)
        for row in records:
            raw = row.data
            if ((options.model and raw.get('model') != options.model) or
                    (options.language and raw.get('language') != options.language)):
                context.stats['filtered_records'] += 1
                continue
            messages = raw.get('conversation')
            if not isinstance(messages,list):
                raise ValueError('LMSYS conversation must be a list')
            source_id = str(raw.get('conversation_id',f'{row.file_index}:{row.row_index}'))
            rng = random.Random(f'lmsys-turn-v1:{options.seed}:{row.file_index}:{row.row_index}:{source_id}')
            history, requests = [], []
            elapsed = 0.
            expecting_user = True
            for msg in messages:
                if (not isinstance(msg,dict) or msg.get('role') not in ('system','user','assistant')
                        or not isinstance(msg.get('content'),str)):
                    raise ValueError('LMSYS messages need system/user/assistant role and string content')
                role = msg['role']
                if role == 'system':
                    if history:
                        raise ValueError('LMSYS system message must be first')
                elif role == 'user':
                    if not expecting_user:
                        raise ValueError('LMSYS user and assistant messages must alternate')
                    expecting_user = False
                else:
                    if expecting_user:
                        raise ValueError('LMSYS assistant response requires a preceding user message')
                    if requests:
                        elapsed += mean if cv == 0 else rng.gammavariate(1/(cv*cv),mean*cv*cv)
                    ids = tokenizer.encode(history)
                    requests.append(dict(timestamp=elapsed, hash_ids=prefix_hashes(ids, size)))
                    expecting_user = True
                history.append(dict(role=role,content=msg['content']))
            if not requests:
                context.stats['conversations_without_responses'] += 1
                continue
            yield dict(requests=requests)
