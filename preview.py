#!/usr/bin/env python3
"""启动 traceGen 本地交互工作台。"""
import argparse
from pathlib import Path
from tracegen.workbench import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--output-dir',type=Path,default=Path('runs/workbench'))
    args = parser.parse_args()
    server = make_server(port=args.port,output=args.output_dir)
    print(f'traceGen: http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
