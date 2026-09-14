#!/usr/bin/env python3
"""生成配置驱动 trace、统计曲线和可选 PNG/SVG；只使用 tasks 协议。"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracegen.analysis import analyze, plot
from tracegen.synthesis import generate
from tracegen.validation import positive


def run_config(config_path, output_dir, window=10, plots=True):
    positive(window, "window")
    config_path, output_dir = Path(config_path).resolve(), Path(output_dir).resolve()
    targets = [output_dir / name for name in ("trace.jsonl", "trace.jsonl.manifest.json",
                                               "config.json", "report.json", "curves.png", "curves.svg")]
    if config_path in targets:
        raise ValueError("output directory must not overwrite the input configuration")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    trace = output_dir / "trace.jsonl"
    manifest = generate(config, trace)
    report = analyze(trace, manifest, window)
    for name, data in (("config.json", config), ("report.json", report)):
        (output_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if plots:
        plot(report, output_dir / "curves")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("runs/synthetic"))
    parser.add_argument("--window", type=float, default=10, help="statistics window in seconds; does not affect trace")
    parser.add_argument("--no-plots", action="store_true", help="generate trace/report using only the standard library")
    args = parser.parse_args()
    try:
        report = run_config(args.config, args.output_dir, args.window, not args.no_plots)
    except (ValueError, TypeError, KeyError, OSError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["stats"], indent=2))
    print(f"Outputs: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
