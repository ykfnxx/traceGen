# traceGen

English | [简体中文](README_ZH.md)

traceGen synthesizes multi-turn LLM serving arrivals directly from task configurations. An overall **session-start intensity** and task mix feed a pool of independent clients. Each session uses its own seed to sample request count, context growth and inter-request gaps. Requests are merged chronologically, with synthetic prefix-dependent block identities.

The new generator requires no dataset, tokenizer or real text. It models arrivals, not inference execution or cache capacity. Preset numbers are illustrative assumptions; papers motivate patterns, with no automatic calibration.

## Run

Python 3.10+. Generation and JSON analysis use only the standard library. Run from the repository root:

```bash
python3 generate.py --config examples/config.example.json --output runs/example/trace.jsonl
python3 experiments/run_synthetic.py --config examples/config.example.json \
  --output-dir runs/example-report --window 10 --no-plots
```

For PNG/SVG plots:

```bash
python3 -m venv .venv
.venv/bin/pip install -r experiments/requirements-plot.txt
.venv/bin/python experiments/run_synthetic.py --config examples/config.example.json \
  --output-dir runs/example-report --window 10
```

Changing `--window` changes statistics only; it does not change the trace.

## Interactive workbench

```bash
npm --prefix web ci
npm --prefix web run build
python3 preview.py
```

Open http://127.0.0.1:8765. Building requires Node.js 22+; serving requires only Python. Assets are served locally with no CDN dependency. Use `--port` or `--output-dir` to change the local port or run directory.

The React workbench edits global/task/client curves, bursts, distributions, prefix groups and complete JSON configs. Dragging updates input curves immediately; release triggers the Python generator. Freeze a baseline to compare changes with the saved configuration and seed. Task/client filters and window changes analyze existing traces; smoothing, log axes and zoom affect display only. Export configuration, full trace, manifest, filtered statistics and SVG plots. See the [workbench guide](docs/workbench.md).


## Configuration

Start with [config.example.json](examples/config.example.json). The [configuration reference](docs/configuration.md) documents every supported field, default and distribution parameterization. [Presets](examples/presets/README.md) cover chat, coding, short Q&A, customer service, sequential research, data analysis, long agents, head clients, daily cycles and bursts.

The daily coding profile uses a Weka-derived three-component Lognormal arrival-gap mixture; see the preset notes for provenance and offline fitting. Runtime generation does not read datasets.

- `version: 3` selects pure configuration synthesis. `traffic.session_rate` is sessions/s, as a constant or a time curve. Request RPS emerges from multi-turn expansion.
- Task and client weights are normalized separately. Local bursts and client activity act after allocation without suppressing other sources. Existing sessions retain their gaps.
- Gamma/Weibull client clocks retain residual integrated intensity through rate changes. Curves are approximated by midpoint constant rates on the `traffic.resolution` grid plus curve/burst knots; reduce resolution to assess numerical convergence.
- Stable session seeds derive from global seed, task/client keys and client-local ordinal. Separate structure, growth, output and timing streams isolate random draws. Changing traffic may change which sessions are emitted, but not the relative trajectory of a matching session identity.
- Input tokens accumulate the previous input, previous output and new external content. A once-per-session growth multiplier affects external increments. Complete blocks are counted after accumulating tokens; partial tails survive across turns.
- Public groups share within global, task or client scope. Private suffixes are session-isolated. Equal lengths do not imply equal content. Prefix identities depend on all preceding blocks.

## Outputs and semantics

`generate.py` writes JSONL (or deterministic `.jsonl.gz`) and `<trace>.manifest.json`. The manifest contains the configuration snapshot, effective client rates, session seeds/identities, planned and emitted counts, cutoff statistics and arrival-lifetime concurrency. Replay is deterministic with the same configuration, generator version and Python random implementation.

Rows contain `timestamp`, `session_id`, `hash_ids`, plus `request_index`, `input_tokens`, `output_tokens` and `external_tokens` by default. Set `output.request_metadata: false` for only the first three fields. On request zero, `external_tokens` is the initial private context; later it is the scaled external increment. Empty block lists are retained.

The output window is `[0, duration)`, starting empty. Requests at/after cutoff are omitted. Active sessions span first through planned last arrival, clipped at cutoff; singleton and zero-duration sessions contribute no active time. This is not inference concurrency. Gaps directly represent successive LLM arrivals without adding response time.

`run_synthetic.py` also writes `config.json`, `report.json`, and optional `curves.png/svg`. Statistics include task/client RPS, gaps, session sizes/durations, token lengths, conditional context quantiles, historical prefix reuse and intervals, token demand, count autocorrelation and gap/growth density. Twelve core panels are rendered. Historical reuse is an opportunity without eviction, not a deployed cache hit rate.

## Scope and compatibility

The configuration core, CLI, JSON analysis, static plots and interactive curve/distribution workbench are implemented. Compression, branches, retries, human-turn hierarchy, admission limits and serving feedback remain outside the [base design](docs/config-driven-refactor.md).

Only version 3 task configs are accepted. Dataset sampling, calibration, old admission/perturbation logic, conversion frontends and their obsolete scripts/tests/examples have been removed. There is a single generation path.

See [paper-pattern validation](docs/paper-pattern-validation.md) for eight manual configurations across three seeds, measured against scoped ServeGen/FineServe statistics. The report distinguishes matching moments from unresolved joint distributions and records censoring and sampling effects. Reproduce with `python3 experiments/validate_paper_patterns.py`; optionally plot with `python3 experiments/plot_paper_patterns.py`.

```bash
python3 -m unittest discover -s tests -q
npm --prefix web run build
npm --prefix web test
```

Tests verify deterministic replay, stream isolation, rate allocation, renewal residuals, ordering/cutoff, token accounting, partial tails, prefix sharing/isolation and analysis-window independence. These checks do not establish production representativeness.

The default report includes `estimated_running_requests`: decode-only concurrency at 50/80/100 tokens/s per request, computed from next-input increments defined as output, without external-token subtraction, with immediate starts (final emitted requests excluded). Plots include wall-time-weighted CDF/PMF and window-average concurrency; the report includes quantiles, peaks and adjacent-request conflicts. This excludes prefill, queueing and concurrency slowdown and is not a serving measurement. Token metadata is required.
