#!/usr/bin/env python3
"""Convert raw datasets: python3 prepare.py <frontend> --input RAW --output SESSIONS.jsonl"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from tracegen.frontends import load_frontend
from tracegen.frontends.common import ConversionContext, fingerprint, records
from tracegen.schema import validate_session


def prepare(frontend, options):
    paths = [Path(p).resolve() for p in options.input]
    output = Path(options.output).resolve()
    manifest_path = Path(str(output)+'.manifest.json')
    if output in paths or manifest_path in paths:
        raise ValueError('output must not overwrite a source dataset')
    if options.limit is not None and options.limit < 1:
        raise ValueError('limit must be positive')
    sources = [dict(path=str(p), sha256=fingerprint(p), bytes=p.stat().st_size) for p in paths]
    context = ConversionContext(sources)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f'.{output.name}.', dir=output.parent)
    manifest_temp = None
    total_requests = sessions = 0
    sha = hashlib.sha256()
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            for session in frontend.convert(records(paths, context, options.limit), options, context):
                validate_session(session)
                line = json.dumps(session, ensure_ascii=False, allow_nan=False, separators=(',',':'))+'\n'
                stream.write(line)
                sha.update(line.encode())
                sessions += 1
                total_requests += len(session['requests'])
        if not sessions:
            raise ValueError('conversion produced no sessions')
        manifest = dict(schema_version=2, block_size=getattr(options, "block_size", None),
                        frontend=options.frontend, sources=sources,
                        options={k: ([str(v) for v in value] if k=='input' else str(value) if isinstance(value,Path) else value)
                                 for k,value in vars(options).items()},
                        output_sha256=sha.hexdigest(), stats=dict(context.stats, sessions=sessions,
                        requests=total_requests), details=context.details)
        fd, manifest_temp = tempfile.mkstemp(prefix=f'.{output.name}.manifest.',dir=output.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temp,output)
        os.replace(manifest_temp,manifest_path)
        return manifest
    finally:
        for path in (temp,manifest_temp):
            if path and os.path.exists(path):
                os.unlink(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('frontend', help='weka, swissai, lmsys, or external module:Class')
    parser.add_argument('--input', type=Path, nargs='+', required=True, help='JSONL[.gz] or Parquet files')
    parser.add_argument('--output', type=Path, required=True, help='normalized uncompressed session JSONL')
    parser.add_argument('--limit', type=int, help='read at most this many raw records')
    try:
        if len(sys.argv) == 1 or sys.argv[1] in ('-h','--help'):
            parser.print_help()
            return 0
        frontend = load_frontend(sys.argv[1])
        frontend.configure(parser)
        options = parser.parse_args()
        print(json.dumps(prepare(frontend,options),indent=2,ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, ImportError, AttributeError) as exc:
        print(f'error: {exc}',file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
