"""合成 trace 的统计曲线；分析不使用随机数，也不修改生成配置。"""

from collections import defaultdict
import gzip
import json
import math
from pathlib import Path

from .validation import positive


def quantile(values, probability):
    if not values:
        return None
    position = (len(values) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def cdf(values):
    ordered = sorted(values)
    if not ordered:
        return []
    return [[quantile(ordered, p / 100), p / 100] for p in range(101)]


def analyze(path, manifest, window=10, task=None, client=None):
    positive(window, "window")
    duration = manifest["config"]["duration"]
    sessions = {s["session_id"]: s for s in manifest["sessions"]
                if (task is None or s['task'] == task) and (client is None or s['client'] == client)}
    bins = [dict(start=i * window, end=min((i + 1) * window, duration), requests=0,
                 tasks=defaultdict(int), clients=defaultdict(int), input_tokens=0, output_tokens=0,
                 blocks=0, reused_blocks=0, lengths=[], starts=0) for i in range(math.ceil(duration / window))]
    values = defaultdict(list)
    turns = defaultdict(list)
    last_request, last_block, previous_blocks = {}, {}, {}
    previous_time = None
    total_blocks, reused_blocks, within_blocks, public_blocks = 0, 0, 0, 0
    metadata = manifest["config"].get("output", {}).get("request_metadata", True)
    joint = []
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            t, sid, hashes = row["timestamp"], row["session_id"], row["hash_ids"]
            if sid not in sessions:
                continue
            session = sessions[sid]
            bucket = bins[min(len(bins) - 1, int(t / window))]
            bucket["requests"] += 1
            bucket['starts'] += sid not in last_request
            bucket["tasks"][session["task"]] += 1
            client_label = json.dumps([session["task"], session["client"]], ensure_ascii=False, separators=(",", ":"))
            bucket["clients"][client_label] += 1
            if previous_time is not None:
                values["arrival_gap"].append(t - previous_time)
            gap = t - last_request[sid] if sid in last_request else None
            if gap is not None:
                values["session_gap"].append(gap)
            previous_time = t
            last_request[sid] = t
            reused = sum(h in last_block for h in hashes)
            within = previous_blocks.get(sid, 0)
            for h in hashes:
                if h in last_block:
                    values["reuse_interval"].append(t - last_block[h])
                last_block[h] = t
            previous_blocks[sid] = len(hashes)
            total_blocks += len(hashes)
            reused_blocks += reused
            within_blocks += within
            public_blocks += min(len(hashes), session["public_prefix_tokens"] // manifest["config"]["block_size"])
            bucket["blocks"] += len(hashes)
            bucket["reused_blocks"] += reused
            if hashes:
                values["reusable_prefix_fraction"].append(reused / len(hashes))
            if "input_tokens" in row:
                for field in ("input_tokens", "output_tokens"):
                    values[field].append(row[field])
                    bucket[field] += row[field]
                turns[row["request_index"]].append(row["input_tokens"])
                bucket['lengths'].append(row['input_tokens'])
                values["public_prefix_fraction"].append(session["public_prefix_tokens"] / row["input_tokens"] if row["input_tokens"] else 0)
                if gap is not None:
                    values["external_tokens"].append(row["external_tokens"])
                    joint.append((gap, row["external_tokens"]))
            else:
                metadata = False
    for bucket in bins:
        length = bucket["end"] - bucket["start"]
        bucket["rps"] = bucket["requests"] / length
        bucket['session_start_rate'] = bucket['starts'] / length
        lengths = sorted(bucket.pop('lengths'))
        bucket['input_quantiles'] = [quantile(lengths, p) for p in (.1, .5, .9)]
        bucket["input_tokens_per_second"] = bucket["input_tokens"] / length if metadata else None
        bucket["output_tokens_per_second"] = bucket["output_tokens"] / length if metadata else None
        bucket["mean_input_tokens"] = bucket["input_tokens"] / bucket["requests"] if metadata and bucket["requests"] else None
        bucket["reusable_prefix_fraction"] = bucket["reused_blocks"] / bucket["blocks"] if bucket["blocks"] else None
        for category in ("tasks", "clients"):
            bucket[category] = {k: v / length for k, v in sorted(bucket[category].items())}
    for session in sessions.values():
        values["planned_requests"].append(session["planned_requests"])
        values["emitted_requests"].append(session["emitted_requests"])
        values["planned_session_duration"].append(session["end"] - session["start"])
        values["observed_session_duration"].append(last_request[session["session_id"]] - session["start"])
    counts = [b["requests"] for b in bins if b["end"] - b["start"] == window]
    mean = sum(counts) / len(counts) if counts else 0
    variance = sum((v - mean)**2 for v in counts)
    autocorrelation = [[lag * window, sum((counts[i] - mean) * (counts[i + lag] - mean)
                           for i in range(len(counts) - lag)) / variance if variance else None]
                       for lag in range(min(31, len(counts)))]
    # 二维 log1p 分箱，不保存或下采样整个请求散点；零值有效。
    density = []
    if joint:
        max_x = max(math.log1p(x) for x, _ in joint) or 1
        max_y = max(math.log1p(y) for _, y in joint) or 1
        grid = defaultdict(int)
        for x, y in joint:
            grid[min(19, int(math.log1p(x) / max_x * 20)), min(19, int(math.log1p(y) / max_y * 20))] += 1
        density = [dict(gap_low=math.expm1(x / 20 * max_x), gap_high=math.expm1((x + 1) / 20 * max_x),
                        tokens_low=math.expm1(y / 20 * max_y), tokens_high=math.expm1((y + 1) / 20 * max_y), count=n)
                   for (x, y), n in sorted(grid.items())]
    stats = dict(manifest['stats'])
    concurrency = manifest['session_concurrency']
    rate_segments = manifest['session_rate_segments']
    mix = manifest['task_mix']
    if task is not None or client is not None:
        selected_clients = [c for c in manifest['clients'] if
                            (task is None or c['task'] == task) and (client is None or c['key'] == client)]
        cursors = [0] * len(selected_clients)
        rate_segments = []
        for segment in manifest['session_rate_segments']:
            midpoint = (segment['start'] + segment['end']) / 2
            effective = defaultdict(float)
            for i, c in enumerate(selected_clients):
                while c['session_rate_segments'][cursors[i]]['end'] <= midpoint:
                    cursors[i] += 1
                effective[c['task']] += c['session_rate_segments'][cursors[i]]['session_rate']
            rate_segments.append(dict(segment, session_rate=sum(effective.values()), tasks=dict(effective)))
        changes = defaultdict(int)
        for s in sessions.values():
            end = min(s['end'], duration)
            if end > s['start']:
                changes[s['start']] += 1
                changes[end] -= 1
        concurrency, active, area, last, peak = [], 0, 0, 0, 0
        for t, delta in sorted(changes.items()):
            area += active * (t-last)
            active += delta
            concurrency.append(dict(timestamp=t, active=active))
            last, peak = t, max(peak, active)
        stats.update(requests=sum(b['requests'] for b in bins), sessions=len(sessions), blocks=total_blocks,
                     input_tokens=sum(b['input_tokens'] for b in bins) if metadata else None,
                     output_tokens=sum(b['output_tokens'] for b in bins) if metadata else None)
        stats['empty_requests'] = stats['requests'] - len(values.get('reusable_prefix_fraction', []))
        stats.update(actual_rps=stats['requests']/duration, actual_session_rate=len(sessions)/duration,
                     mean_concurrent_sessions=area/duration, peak_concurrent_sessions=peak,
                     truncated_sessions=sum(s['emitted_requests'] < s['planned_requests'] for s in sessions.values()),
                     omitted_requests_at_end=sum(s['planned_requests']-s['emitted_requests'] for s in sessions.values()))
        mix = [dict(key=k, requests=round(sum(b['tasks'].get(k,0)*(b['end']-b['start']) for b in bins)),
                    sessions=sum(s['task']==k for s in sessions.values())) for k in sorted({s['task'] for s in sessions.values()})]
    return dict(window_seconds=window, request_metadata=metadata, filter=dict(task=task, client=client),
                stats=stats, task_mix=mix,
                historical_prefix_reuse=dict(block_references=total_blocks, reused_blocks=reused_blocks,
                    within_session_blocks=within_blocks, extra_cross_session_blocks=reused_blocks - within_blocks,
                    public_block_references=public_blocks, unique_blocks=len(last_block),
                    fraction=reused_blocks / total_blocks if total_blocks else 0,
                    semantics="historical opportunity without eviction, capacity or TTL; not cache hit rate"),
                rate_segments=rate_segments, concurrency=concurrency,
                time_series=bins, cdfs={k: cdf(v) for k, v in sorted(values.items())},
                context_by_request=[dict(request_index=k, samples=len(v),
                    p10=quantile(sorted(v), .1), p50=quantile(sorted(v), .5), p90=quantile(sorted(v), .9))
                    for k, v in sorted(turns.items())],
                count_autocorrelation=autocorrelation, gap_growth_density=density)


def plot(report, output):
    """可选 Matplotlib 导出；运行核心和统计不依赖绘图库。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.colors import LogNorm
    fig, axes = plt.subplots(4, 3, figsize=(17, 16), constrained_layout=True)
    axes = axes.flat
    series, rates = report["time_series"], report["rate_segments"]
    t = [b["start"] for b in series]
    def step(ax, x, y, label):
        if x:
            if x[-1] < rates[-1]["end"]:
                x, y = x + [rates[-1]["end"]], y + [y[-1]]
            ax.step(x, y, where="post", label=label)
    x = [s["start"] for s in rates] + [rates[-1]["end"]]
    for key, label in [("configured_session_rate", "Overall curve + global bursts"), ("session_rate", "Effective client pool")]:
        step(axes[0], x, [s[key] for s in rates] + [rates[-1][key]], label)
    axes[0].set(title="Session starts: configured intensity", ylabel="sessions/s", xlabel="seconds")
    for task in report["task_mix"]:
        key = task["key"]
        step(axes[1], t, [b["tasks"].get(key, 0) for b in series], key)
    step(axes[1], t, [b["rps"] for b in series], "total")
    axes[1].set(title=f"Realized requests ({report['window_seconds']:g}s bins)", ylabel="requests/s", xlabel="seconds")
    concurrency = report["concurrency"]
    step(axes[2], [0] + [s["timestamp"] for s in concurrency] + [rates[-1]["end"]],
         [0] + [s["active"] for s in concurrency] + [0], "active sessions")
    axes[2].set(title="Active sessions (arrival lifetime)", xlabel="seconds")
    def plot_cdf(ax, names, title, log=False):
        for name in names:
            points = report["cdfs"].get(name, [])
            if points:
                ax.plot([p[0] for p in points], [p[1] for p in points], label=name)
        ax.set(title=title, ylabel="CDF")
        if log: ax.set_xscale("symlog", linthresh=.01)
    plot_cdf(axes[3], ["arrival_gap", "session_gap"], "Inter-arrival gaps (seconds)", True)
    plot_cdf(axes[4], ["planned_requests", "emitted_requests"], "Session request count")
    plot_cdf(axes[5], ["planned_session_duration", "observed_session_duration"], "Session duration (seconds)", True)
    context = report["context_by_request"]
    if context:
        turn = [c["request_index"] for c in context]
        axes[6].fill_between(turn, [c["p10"] for c in context], [c["p90"] for c in context], alpha=.2, label="p10-p90")
        axes[6].plot(turn, [c["p50"] for c in context], label="p50")
    axes[6].set(title="Context, conditional on reaching request index", xlabel="request index (zero based)", ylabel="input tokens")
    plot_cdf(axes[7], ["input_tokens", "output_tokens", "external_tokens"], "Request lengths (tokens)", True)
    plot_cdf(axes[8], ["public_prefix_fraction", "reusable_prefix_fraction"], "Public vs historically reusable prefix fraction")
    for key in ("input_tokens_per_second", "output_tokens_per_second"):
        step(axes[9], t, [b[key] for b in series], key)
    axes[9].set(title="Arrival token demand", xlabel="seconds", ylabel="tokens/s")
    acf = report["count_autocorrelation"]
    if acf: axes[10].plot([p[0] for p in acf], [p[1] for p in acf], marker=".")
    axes[10].set(title="Request-count ACF (full bins only)", xlabel="lag (seconds)")
    density = report["gap_growth_density"]
    norm = LogNorm(vmin=1, vmax=max(2, max((d["count"] for d in density), default=2)))
    for d in density:
        axes[11].add_patch(Rectangle((d["gap_low"], d["tokens_low"]),
            d["gap_high"] - d["gap_low"], d["tokens_high"] - d["tokens_low"],
            facecolor=plt.cm.viridis(norm(d["count"]))))
    axes[11].autoscale_view()
    axes[11].set(xscale="symlog", yscale="symlog", title="Gap vs external growth (log1p bins)", xlabel="gap (s)", ylabel="external tokens")
    axes[11].set_xlim(left=0)
    axes[11].set_ylim(bottom=0)
    if density:
        fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap="viridis"), ax=axes[11], label="requests")
    for ax in axes:
        ax.grid(alpha=.2)
        if ax.get_legend_handles_labels()[0]: ax.legend(fontsize=7)
    fig.suptitle("traceGen — configuration-generated workload (illustrative assumptions, no calibration)", fontsize=15)
    output = Path(output)
    for suffix in (".png", ".svg"):
        fig.savefig(output.with_suffix(suffix), dpi=150)
    plt.close(fig)
