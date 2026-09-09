#!/usr/bin/env python3
"""Analyze existing experiment outputs; requires matplotlib and numpy."""

import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np


COLORS = ["#2878B5", "#D98220", "#C74455"]


def describe(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {"n": 0}
    return {
        "n": len(values), "mean": float(values.mean()), "min": float(values.min()),
        **{f"p{q}": float(np.percentile(values, q)) for q in (50, 90, 95, 99)},
        "max": float(values.max()),
        "cv": float(values.std() / values.mean()) if values.mean() else None,
    }


def read_run(path):
    manifest = json.loads(Path(str(path) + ".manifest.json").read_text())
    timestamps, block_counts = [], []
    by_session = defaultdict(list)
    fingerprint = hashlib.sha256()
    with path.open("rb") as stream:
        for line in stream:
            fingerprint.update(line)
            row = json.loads(line)
            timestamps.append(row["timestamp"])
            block_counts.append(len(row["hash_ids"]))
            by_session[row["session_id"]].append(row["timestamp"])
    if fingerprint.hexdigest() != manifest["output_sha256_uncompressed"]:
        raise ValueError(f"checksum mismatch: {path}")
    times = np.asarray(timestamps)
    duration = manifest["config"]["duration"]
    if not len(times) or np.any(np.diff(times) < 0) or times[0] < 0 or times[-1] >= duration:
        raise ValueError(f"empty or invalid request timeline: {path}")
    if len(times) != manifest["stats"]["requests"] or sum(block_counts) != manifest["stats"]["blocks"]:
        raise ValueError(f"manifest counts mismatch: {path}")
    for session in manifest["sessions"]:
        if len(by_session[session["session_id"]]) != session["emitted_requests"]:
            raise ValueError("session request count mismatch")
    session_gaps = np.concatenate([np.diff(t) for t in by_session.values()])
    session_starts = np.asarray([s["start"] for s in manifest["sessions"]])
    edges = np.append(np.arange(0, duration, 60), duration)
    counts, _ = np.histogram(times, edges)
    if int(counts.sum()) != len(times):
        raise ValueError("time histogram lost requests")
    rate = counts / np.diff(edges)
    # No serving completion information: do not label this as runtime concurrency.
    ends = [min(duration, s["start"] + s["template_duration"] / manifest["config"]["load_scale"])
            for s in manifest["sessions"]]
    awaiting = [int(sum(start <= t < end for start, end in zip(session_starts, ends)))
                for t in (duration / 4, duration / 2, duration * 3 / 4)]
    summary = {
        "load_scale": manifest["config"]["load_scale"], "duration_seconds": duration,
        "requests": len(times), "sessions": len(by_session),
        "unique_templates": manifest["stats"]["unique_templates"],
        "mean_rps": len(times) / duration, "peak_60s_rps": float(rate.max()),
        "peak_to_mean_60s": float(rate.max() / (len(times) / duration)),
        "first_quarter_rps": float(np.count_nonzero(times < duration / 4) / (duration / 4)),
        "last_quarter_rps": float(np.count_nonzero(times >= 3 * duration / 4) / (duration / 4)),
        "empty_60s_bins": int(np.count_nonzero(counts == 0)),
        "global_request_iat_seconds": describe(np.diff(times)),
        "within_session_request_iat_seconds": describe(session_gaps),
        "session_start_iat_seconds": describe(np.diff(session_starts)),
        "blocks_per_request": describe(block_counts),
        "emitted_requests_per_session": describe([len(t) for t in by_session.values()]),
        "truncated_sessions": manifest["stats"]["truncated_sessions"],
        "truncated_session_fraction": manifest["stats"]["truncated_sessions"] / len(by_session),
        "omitted_requests": manifest["stats"]["omitted_requests_at_end"],
        "emitted_fraction_of_sampled_templates": len(times) / sum(
            s["template_requests"] for s in manifest["sessions"]),
        "sessions_awaiting_scheduled_requests_at_quarters": awaiting,
        "trace_sha256": fingerprint.hexdigest(),
    }
    return dict(summary=summary, times=times, blocks=block_counts, by_session=by_session,
                manifest=manifest, edges=edges, rate=rate, session_gaps=session_gaps)


def style():
    font_path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if font_path.exists():
        font_manager.fontManager.addfont(font_path)
        plt.rcParams["font.family"] = ["DejaVu Sans",
                                       font_manager.FontProperties(fname=font_path).get_name()]
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.unicode_minus": False, "axes.titleweight": "bold",
                         "axes.grid": True, "grid.alpha": 0.18, "savefig.facecolor": "white"})


def ecdf(ax, values, label, color):
    values = np.sort(values)
    positive = values > 0
    if np.any(positive):
        ax.step(values[positive], (np.arange(len(values)) + 1)[positive] / len(values),
                where="post", label=label, color=color, linewidth=1.8)
    ax.set_xscale("log")
    ax.set_ylim(0, 1.01)
    ax.set_ylabel("累计请求间隔比例")
    ax.set_xlabel("相邻请求间隔（秒，对数坐标）")


def save(fig, output, name):
    for suffix in ("png", "pdf"):
        fig.savefig(output / f"{name}.{suffix}", dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot(runs, output):
    style()
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9), layout="constrained")
    config = runs[0]["manifest"]["config"]
    fig.suptitle("合成请求的时序分布\n"
                 f"{config['duration'] / 60:g} 分钟窗口 · block_size={config['block_size']}"
                 f" · seed={config['seed']} · 从空载开始", fontsize=17)
    for run, color in zip(runs, COLORS):
        s = run["summary"]
        label = f"{s['load_scale']:g}×  ({s['requests']:,} 请求)"
        axes[0, 0].stairs(run["rate"], run["edges"] / 60, baseline=None,
                          color=color, label=label, linewidth=1.7)
        axes[0, 1].step(np.r_[0, run["times"], s["duration_seconds"]] / 60,
                        np.r_[0, np.arange(1, len(run["times"]) + 1), len(run["times"])],
                        where="post", color=color, linewidth=2, label=label)
        ecdf(axes[1, 0], np.diff(run["times"]), label, color)
        ecdf(axes[1, 1], run["session_gaps"], label, color)
    axes[0, 0].set(title="A. 请求到达率（每 60 秒分桶）", xlabel="合成时间（分钟）", ylabel="请求数 / 秒")
    axes[0, 1].set(title="B. 累计请求数", xlabel="合成时间（分钟）", ylabel="请求数")
    axes[1, 0].set_title("C. 全局相邻请求间隔")
    axes[1, 1].set_title("D. 同一 session 内相邻请求间隔")
    axes[0, 0].legend(loc="upper left", frameon=False)
    for ax in axes[0]:
        ax.set_xlim(0, max(r["summary"]["duration_seconds"] for r in runs) / 60)
        ax.set_ylim(bottom=0)
    save(fig, output, "request_timing")

    fig, axes = plt.subplots(len(runs), 1, figsize=(14, 12), sharex=True, layout="constrained",
                             gridspec_kw={"height_ratios": [max(15, r["summary"]["sessions"]) for r in runs]})
    fig.suptitle("逐 session 请求到达时间\n每个短竖线是一条请求；每行一个 session；红色 > 表示截止时仍有后续请求", fontsize=16)
    for ax, run, color in zip(np.atleast_1d(axes), runs, COLORS):
        metadata = run["manifest"]["sessions"]
        scale, duration = run["summary"]["load_scale"], run["summary"]["duration_seconds"]
        for i, s in enumerate(metadata):
            xs = np.asarray(run["by_session"][s["session_id"]]) / 60
            end = min(duration, s["start"] + s["template_duration"] / scale)
            ax.hlines(i, s["start"] / 60, end / 60, color=color, alpha=0.13, linewidth=2)
            ax.plot(xs, np.full(len(xs), i), "|", markersize=5, markeredgewidth=0.7, color=color)
            if s["emitted_requests"] < s["template_requests"]:
                ax.plot(duration / 60, i, ">", color="#C74455", markersize=4, clip_on=False)
        ax.set(title=f"{scale:g}×：{len(metadata)} 个 session，{run['summary']['requests']:,} 个请求",
               ylabel="session 序号", ylim=(len(metadata), -1), xlim=(0, duration / 60))
        ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True, nbins=8))
    np.atleast_1d(axes)[-1].set_xlabel("合成时间（分钟）")
    save(fig, output, "session_raster")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("runs/weka/report.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("runs/weka/distributions"))
    args = parser.parse_args()
    source_report = json.loads(args.report.read_text())
    runs = [read_run(args.report.parent / Path(r["path"]).name) for r in source_report["runs"]]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot(runs, args.output_dir)
    summaries = [r["summary"] for r in runs]
    (args.output_dir / "summary.json").write_text(json.dumps({
        "analysis": "empirical statistics; percentiles use linear interpolation; CV uses population std",
        "iat": "consecutive observed requests only; start/end censored intervals excluded",
        "runs": summaries,
    }, indent=2) + "\n")
    with (args.output_dir / "arrival_rate_60s.csv").open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(["load_scale", "start_seconds", "end_seconds", "requests_per_second"])
        for run in runs:
            for start, end, rate in zip(run["edges"][:-1], run["edges"][1:], run["rate"]):
                writer.writerow([run["summary"]["load_scale"], start, end, rate])
    print(json.dumps(summaries, indent=2))
    print(f"Figures and statistics: {args.output_dir}")


if __name__ == "__main__":
    main()
