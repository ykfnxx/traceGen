import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Chart,
  curvePoints,
  sampleCurve,
  fmt,
  smooth,
  palette,
} from "./charts";
import { Inspector, api, clone, Field } from "./editors";
import "./style.css";

const presetNames = {
  mixed: "混合示例",
  chat: "交互 Chat",
  coding_agent: "Coding Agent",
  long_agent: "长任务 Agent",
  head_clients: "头部 Client",
  daily_mixed: "日周期混合",
  burst_mixed: "Burst 混合",
};
function save(name, data, type = "application/json") {
  const url = URL.createObjectURL(
    new Blob(
      [typeof data === "string" ? data : JSON.stringify(data, null, 2)],
      { type },
    ),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function getCurve(config, target) {
  const task = config.tasks[target.task];
  const client = Array.isArray(task?.clients)
    ? task.clients[target.client]
    : null;
  return target.type === "task"
    ? (task?.weight ?? 1)
    : target.type === "client"
      ? (client?.weight ?? 1)
      : target.type === "activity"
        ? (client?.activity ?? 1)
        : config.traffic.session_rate;
}
function putCurve(config, target, value) {
  if (target.type === "global") config.traffic.session_rate = value;
  else if (target.type === "task") config.tasks[target.task].weight = value;
  else
    config.tasks[target.task].clients[target.client][
      target.type === "activity" ? "activity" : "weight"
    ] = value;
}
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
  return value;
}
function App() {
  const [presets, setPresets] = useState([]),
    [config, setConfig] = useState(null),
    [preset, setPreset] = useState("mixed"),
    [tab, setTab] = useState("整体"),
    [taskIndex, setTaskIndex] = useState(0),
    [clientIndex, setClientIndex] = useState(0),
    [target, setTarget] = useState({ type: "global" });
  const [current, setCurrent] = useState(null),
    [baseline, setBaseline] = useState(null),
    [view, setView] = useState(null),
    [baseView, setBaseView] = useState(null),
    [busy, setBusy] = useState(false),
    [analyzing, setAnalyzing] = useState(false),
    [error, setError] = useState(""),
    [dragging, setDragging] = useState(false),
    [revision, setRevision] = useState(0);
  const [windowSize, setWindowSize] = useState(10),
    [taskFilter, setTaskFilter] = useState(""),
    [clientFilter, setClientFilter] = useState(""),
    [smoothing, setSmoothing] = useState(1),
    [logAxis, setLogAxis] = useState(false),
    [page, setPage] = useState("到达与组成"),
    [zoom, setZoom] = useState(null),
    [showBaseline, setShowBaseline] = useState(true);
  const [analysisError, setAnalysisError] = useState("");
  const upload = useRef(null);
  useEffect(() => {
    fetch("/api/presets")
      .then((r) => r.json())
      .then((data) => {
        setPresets(data);
        setConfig(data[0].config);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!config || dragging) return;
    const controller = new AbortController();
    setBusy(true);
    const timer = setTimeout(
      () =>
        api("run", { config, window: windowSize }, controller.signal)
          .then((data) => {
            setCurrent(data);
            setError("");
            setBusy(false);
          })
          .catch((e) => {
            if (e.name !== "AbortError") {
              setError(e.message);
              setBusy(false);
            }
          }),
      450,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [config, dragging, revision]);
  useEffect(() => {
    if (!current) return;
    const controller = new AbortController();
    setAnalyzing(true);
    Promise.all([
      api(
        "analyze",
        {
          run_id: current.run_id,
          window: windowSize,
          task: taskFilter,
          client: clientFilter,
        },
        controller.signal,
      ),
      baseline
        ? api(
            "analyze",
            {
              run_id: baseline.run_id,
              window: windowSize,
              task: taskFilter,
              client: clientFilter,
            },
            controller.signal,
          )
        : Promise.resolve(null),
    ])
      .then(([a, b]) => {
        setView(a);
        setBaseView(b);
        setAnalyzing(false);
        setAnalysisError("");
      })
      .catch((e) => {
        if (e.name !== "AbortError") {
          setAnalysisError(e.message);
          setAnalyzing(false);
        }
      });
    return () => controller.abort();
  }, [current, baseline, windowSize, taskFilter, clientFilter]);
  const replaceConfig = (c) => {
    if (!c.tasks?.length || !c.traffic)
      throw Error("需要有效的 tasks 和 traffic 配置");
    setConfig(c);
    setTaskIndex(0);
    setClientIndex(0);
    setTaskFilter("");
    setClientFilter("");
    setTarget({ type: "global" });
    setZoom(null);
  };
  const exportSVG = () => {
    const nodes = [...document.querySelectorAll("main .chart")];
    let y = 0;
    const parts = nodes.map((n) => {
      const svg = n.querySelector("svg").cloneNode(true),
        h =
          (Number(svg.getAttribute("viewBox").split(" ")[3]) * 900) /
          Number(svg.getAttribute("viewBox").split(" ")[2]);
      svg.setAttribute("y", y + 30);
      svg.setAttribute("width", "900");
      svg.setAttribute("height", h);
      const title = n
        .querySelector("h3")
        .textContent.replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;");
      const out =
        `<text x="20" y="${y + 22}" font-size="16" fill="#173055">${title}</text>` +
        new XMLSerializer().serializeToString(svg);
      y += h + 50;
      return out;
    });
    save(
      "tracegen-charts.svg",
      `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="${y}" style="background:white;font-family:sans-serif"><style>text{font-size:12px;fill:#56677d}.grid{stroke:#e3e8ef}.handle{fill:white;stroke:#039c9f;stroke-width:2}</style>${parts.join("")}</svg>`,
      "image/svg+xml",
    );
  };
  if (!config) return <p className="loading">{error || "正在加载配置…"}</p>;
  const report = view?.report,
    old = showBaseline ? baseView?.report : null;
  const curve = getCurve(config, target),
    points = curvePoints(curve, config.duration);
  const targetTitle = {
    global: "Session 发起强度",
    task: "Task 发起权重",
    client: "Client 发起权重",
    activity: "Client 活跃倍率",
  }[target.type];
  const oldTaskIndex = baseline?.config.tasks.findIndex(
    (t) => t.key === config.tasks[target.task]?.key,
  );
  const oldClients = baseline?.config.tasks[oldTaskIndex]?.clients;
  const activeClient = config.tasks[target.task]?.clients?.[target.client];
  const oldClientIndex = Array.isArray(oldClients)
    ? oldClients.findIndex((c) => c.key === activeClient?.key)
    : -1;
  const oldCurve = !baseline
    ? null
    : target.type === "global"
      ? baseline.config.traffic.session_rate
      : oldTaskIndex < 0 ||
          (["client", "activity"].includes(target.type) && oldClientIndex < 0)
        ? null
        : getCurve(baseline.config, {
            ...target,
            task: oldTaskIndex,
            client: oldClientIndex,
          });
  const domain = zoom || [0, current?.config.duration || config.duration];
  const timePoints = (r, key) => {
    const bins = r?.time_series || [];
    if (!bins.length) return [];
    const a = bins.map((b) => [
      b.start,
      typeof key === "function" ? key(b) : b[key],
    ]);
    a.push([bins.at(-1).end, a.at(-1)[1]]);
    return smooth(a, smoothing);
  };
  const timeSeries = (key, label = "当前") => [
    { label, points: timePoints(report, key), color: palette[0], step: true },
    ...(old
      ? [
          {
            label: "对比基线",
            points: timePoints(old, key),
            color: "#e18b36",
            dash: true,
            step: true,
          },
        ]
      : []),
  ];
  const cdfSeries = (fields) =>
    fields
      .map((f, i) => ({
        label: typeof f === "string" ? f : f[1],
        points: report?.cdfs[typeof f === "string" ? f : f[0]] || [],
        color: palette[i % palette.length],
      }))
      .concat(
        old
          ? fields.map((f, i) => ({
              label: "基线 " + (typeof f === "string" ? f : f[1]),
              points: old.cdfs[typeof f === "string" ? f : f[0]] || [],
              color: "#e18b36",
              dash: true,
            }))
          : [],
      );
  const context = report?.context_by_request || [];
  const contextChart = (
    <Chart
      title="上下文随请求序号增长"
      xlabel="LLM 请求序号（从 0 开始）"
      ylabel="input tokens"
      height={150}
      logY={logAxis}
      band={{
        upper: context.map((c) => [c.request_index, c.p90]),
        lower: context.map((c) => [c.request_index, c.p10]),
      }}
      series={[
        {
          label: "P50 · 阴影 P10–P90",
          points: context.map((c) => [c.request_index, c.p50]),
        },
        ...(old
          ? [
              {
                label: "基线 P50",
                color: "#e18b36",
                dash: true,
                points: old.context_by_request.map((c) => [
                  c.request_index,
                  c.p50,
                ]),
              },
            ]
          : []),
      ]}
    />
  );
  const cdfChart = (title, fields, xlabel = "值", height = 190) => (
    <Chart
      title={title}
      series={cdfSeries(fields)}
      xlabel={xlabel}
      ylabel="CDF"
      height={height}
      logX={logAxis}
    />
  );
  const clientOptions = (current?.clients || []).filter(
    (c) => !taskFilter || c.task === taskFilter,
  );
  const clientKeys = [...new Set(clientOptions.map((c) => c.key))];
  const stale = JSON.stringify(canonical(current?.config)) !== JSON.stringify(canonical(config));
  return (
    <>
      <header>
        <div className="brand">traceGen</div>
        <div className="product-name">负载配置工作台</div>
        <div className="header-actions">
          <button onClick={() => upload.current.click()}>导入配置</button>
          <button onClick={() => save("tracegen.config.json", config)}>
            导出配置
          </button>
          <button
            className="primary"
            onClick={() => setRevision((x) => x + 1)}
            disabled={busy}
          >
            生成预览
          </button>
        </div>
        <input
          ref={upload}
          hidden
          type="file"
          accept=".json,application/json"
          aria-label="导入配置文件"
          onChange={async (e) => {
            try {
              const imported = JSON.parse(await e.target.files[0].text());
              await api("validate", { config: imported });
              replaceConfig(imported);
              setPreset("custom");
            } catch (e) {
              setError(e.message);
            }
            e.target.value = "";
          }}
        />
      </header>
      <div className="workspace">
        <Inspector
          {...{
            config,
            setConfig,
            tab,
            setTab,
            taskIndex,
            setTaskIndex,
            clientIndex,
            setClientIndex,
            target,
            setTarget,
          }}
          onDragState={setDragging}
        />
        <main>
          <div className="main-heading">
            <div>
              <h1>请求到达与多轮行为</h1>
              <p>配置 sessions/s，观察 serving requests/s</p>
            </div>
            <select
              aria-label="参考预设"
              value={preset}
              onChange={(e) => {
                const p = presets.find((p) => p.key === e.target.value);
                setPreset(p.key);
                replaceConfig(clone(p.config));
              }}
            >
              {preset === "custom" && <option value="custom">导入配置</option>}
              {presets.map((p) => (
                <option key={p.key} value={p.key}>
                  {presetNames[p.key] || p.key}
                </option>
              ))}
            </select>
          </div>
          <div className="toolbar">
            <select
              aria-label="任务筛选"
              value={taskFilter}
              onChange={(e) => {
                setTaskFilter(e.target.value);
                setClientFilter("");
              }}
            >
              <option value="">全部任务</option>
              {(current?.config.tasks || config.tasks).map((t) => (
                <option key={t.key} value={t.key}>
                  {t.key}
                </option>
              ))}
            </select>
            <select
              aria-label="Client 筛选"
              disabled={!taskFilter}
              value={clientFilter}
              onChange={(e) => setClientFilter(e.target.value)}
            >
              <option value="">全部 client</option>
              {clientKeys.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <label>
              窗口{" "}
              <select
                aria-label="统计窗口"
                value={windowSize}
                onChange={(e) => setWindowSize(Number(e.target.value))}
              >
                {[1, 10, 60].map((n) => (
                  <option key={n} value={n}>
                    {n} s
                  </option>
                ))}
              </select>
            </label>
            <label>
              平滑{" "}
              <select
                aria-label="平滑窗口数"
                value={smoothing}
                onChange={(e) => setSmoothing(Number(e.target.value))}
              >
                {[1, 3, 5, 10].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
            <label className="check">
              <input
                aria-label="对数轴"
                type="checkbox"
                checked={logAxis}
                onChange={(e) => setLogAxis(e.target.checked)}
              />
              对数轴
            </label>
            <button
              disabled={!current || busy || stale}
              onClick={() => {
                setBaseline(current);
                setShowBaseline(true);
              }}
            >
              固定为对比基线
            </button>
          </div>
          <div className="metrics" data-testid="metrics">
            <span>{fmt(report?.stats.sessions)} sessions</span>
            <span>{fmt(report?.stats.requests)} requests</span>
            <span>{fmt(report?.stats.actual_rps)} requests/s</span>
            <span>
              {fmt(report?.stats.peak_concurrent_sessions)} 峰值活跃 session
            </span>
          </div>
          <div className="status" role="status" data-testid="status">
            {error || analysisError ? (
              <span className="error" role="alert">
                {error || analysisError}
              </span>
            ) : dragging ? (
              "拖动中 · 松开后生成"
            ) : busy ? (
              "正在生成 · 当前图表保留上次结果"
            ) : analyzing ? (
              "正在统计已有 trace"
            ) : stale ? (
              "配置尚未成功生成"
            ) : current ? (
              "已生成 · " + current.sha256.slice(0, 12)
            ) : (
              "等待生成"
            )}
            {baseline && (
              <span className="baseline-controls">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={showBaseline}
                    onChange={(e) => setShowBaseline(e.target.checked)}
                  />
                  基线 Seed {baseline.config.seed ?? 0} / 当前{" "}
                  {config.seed ?? 0}
                </label>
                <button
                  onClick={() => {
                    setBaseline(null);
                    setBaseView(null);
                  }}
                >
                  清除基线
                </button>
                <button
                  onClick={() => save("baseline.config.json", baseline.config)}
                >
                  导出基线配置
                </button>
              </span>
            )}
          </div>
          <Chart
            title={`${targetTitle} · 拖动控制点`}
            testId="editable-curve"
            ylabel={target.type === "global" ? "sessions/s" : "权重 / 倍率"}
            domain={[0, config.duration]}
            height={175}
            handles={points}
            onDragState={setDragging}
            onHandle={(index, [x, y]) => {
              const c = clone(config),
                p = clone(points);
              const lo = index ? points[index - 1][0] + 0.001 : 0,
                hi =
                  index < points.length - 1
                    ? points[index + 1][0] - 0.001
                    : curve.period || config.duration;
              p[index] = [
                Math.round(Math.max(lo, Math.min(hi, x)) * 1000) / 1000,
                Math.round(y * 10000) / 10000,
              ];
              if (curve.period && index === 0) p[index][0] = 0;
              putCurve(c, target, {
                ...(typeof curve === "object" ? curve : {}),
                points: p,
              });
              setConfig(c);
            }}
            series={[
              {
                label:
                  target.type === "global"
                    ? "配置曲线（不含 burst）"
                    : "配置权重",
                points: sampleCurve(curve, config.duration),
                step: curve.interpolation === "previous",
              },
              ...(oldCurve != null && showBaseline
                ? [
                    {
                      label: "对比基线",
                      points: sampleCurve(oldCurve, config.duration),
                      dash: true,
                      color: "#e18b36",
                    },
                  ]
                : []),
            ]}
          />
          <div className="zoom-controls">
            <span>时间局部放大（或拖选结果图）</span>
            <input
              aria-label="放大开始"
              type="number"
              min="0"
              value={domain[0]}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (v >= 0 && v < domain[1]) setZoom([v, domain[1]]);
              }}
            />
            <span>—</span>
            <input
              aria-label="放大结束"
              type="number"
              value={domain[1]}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (v > domain[0]) setZoom([domain[0], v]);
              }}
            />
            <button onClick={() => setZoom(null)}>重置</button>
          </div>
          <Chart
            title="Serving 请求到达率"
            height={175}
            testId="rps-chart"
            ylabel="requests/s"
            domain={domain}
            logY={logAxis}
            series={timeSeries("rps", "总计")}
            stack={(report?.task_mix || []).map((t) => ({
              label: t.key,
              points: timePoints(report, (b) => b.tasks[t.key] || 0),
            }))}
            onBrush={setZoom}
          />
          <div className="two-charts">
            {contextChart}
            {cdfChart(
              "请求间隔 CDF",
              [
                ["arrival_gap", "聚合 IAT"],
                ["session_gap", "Session 内间隔"],
              ],
              "请求间隔 / s",
              150,
            )}
          </div>
          <nav className="tabs results-tabs">
            {["到达与组成", "会话与增长", "前缀与属性"].map((p) => (
              <button
                key={p}
                className={p === page ? "selected" : ""}
                onClick={() => setPage(p)}
              >
                {p}
              </button>
            ))}
          </nav>
          {report && page === "到达与组成" && (
            <>
              <div className="two-charts">
                <Chart
                  title="有效发起强度与首请求率"
                  domain={domain}
                  ylabel="sessions/s"
                  series={[
                    {
                      label: "有效 client pool",
                      points: report.rate_segments.flatMap((s) => [
                        [s.start, s.session_rate],
                        [s.end, s.session_rate],
                      ]),
                    },
                    ...timeSeries("session_start_rate", "实际首请求"),
                    ...(old
                      ? [
                          {
                            label: "基线有效强度",
                            points: old.rate_segments.flatMap((s) => [
                              [s.start, s.session_rate],
                              [s.end, s.session_rate],
                            ]),
                            dash: true,
                            color: "#da9138",
                          },
                        ]
                      : []),
                  ]}
                  onBrush={setZoom}
                />
                <Chart
                  title="活跃 Session"
                  domain={domain}
                  ylabel="sessions"
                  series={[
                    {
                      label: "当前",
                      points: [
                        [0, 0],
                        ...report.concurrency.map((p) => [
                          p.timestamp,
                          p.active,
                        ]),
                        [current.config.duration, 0],
                      ],
                      step: true,
                    },
                    ...(old
                      ? [
                          {
                            label: "基线",
                            points: [
                              [0, 0],
                              ...old.concurrency.map((p) => [
                                p.timestamp,
                                p.active,
                              ]),
                              [baseline.config.duration, 0],
                            ],
                            color: "#e18b36",
                            dash: true,
                            step: true,
                          },
                        ]
                      : []),
                  ]}
                />
              </div>
              <Chart
                title="Client 流量"
                domain={domain}
                ylabel="requests/s"
                series={[report, ...(old ? [old] : [])].flatMap((r, j) =>
                  Object.keys(
                    r.time_series.reduce(
                      (a, b) => Object.assign(a, b.clients),
                      {},
                    ),
                  ).map((k, i) => ({
                    label: (j ? "基线 " : "") + JSON.parse(k).join(" / "),
                    points: timePoints(r, (b) => b.clients[k] || 0),
                    color: palette[i % palette.length],
                    dash: !!j,
                    step: true,
                  })),
                )}
              />
              <Chart
                title="窗口请求计数自相关"
                xlabel="滞后 / s"
                ylabel="ACF"
                series={[
                  { label: "当前", points: report.count_autocorrelation },
                  ...(old
                    ? [
                        {
                          label: "基线",
                          points: old.count_autocorrelation,
                          dash: true,
                          color: "#e18b36",
                        },
                      ]
                    : []),
                ]}
              />
              <section className="composition">
                <h3>任务组成 · client 数、session 数与请求数分别统计</h3>
                <table>
                  <thead>
                    <tr>
                      <th>Task</th>
                      <th>Client 数量</th>
                      <th>Session 数 / 占比</th>
                      <th>请求数 / 占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.task_mix.map((t) => (
                      <tr key={t.key}>
                        <td>{t.key}</td>
                        <td>
                          {
                            clientOptions.filter(
                              (c) =>
                                c.task === t.key &&
                                (!clientFilter || c.key === clientFilter),
                            ).length
                          }
                        </td>
                        <td>
                          {t.sessions} /{" "}
                          {fmt(
                            (t.sessions / Math.max(1, report.stats.sessions)) *
                              100,
                          )}
                          %
                        </td>
                        <td>
                          {t.requests} /{" "}
                          {fmt(
                            (t.requests / Math.max(1, report.stats.requests)) *
                              100,
                          )}
                          %
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </>
          )}
          {report && page === "会话与增长" && (
            <>
              <div className="two-charts">
                {cdfChart(
                  "Session 请求数量",
                  [
                    ["planned_requests", "计划"],
                    ["emitted_requests", "窗口内"],
                  ],
                  "LLM 请求数",
                )}
                {cdfChart(
                  "Session 持续时间",
                  [
                    ["planned_session_duration", "计划"],
                    ["observed_session_duration", "观察到"],
                  ],
                  "秒",
                )}
                {cdfChart(
                  "请求长度分布",
                  [
                    ["input_tokens", "输入"],
                    ["output_tokens", "输出"],
                    ["external_tokens", "外部新增"],
                  ],
                  "tokens",
                )}
                {cdfChart(
                  "轮间时间分布",
                  [["session_gap", "直接到达间隔"]],
                  "秒",
                )}
              </div>
              <Chart
                title="间隔—外部增长联合分布"
                xlabel="到达间隔 / s"
                ylabel="外部新增 tokens"
                density={report.gap_growth_density}
                logX={logAxis}
                logY={logAxis}
              />
              <p className="hint">
                上下文分位数只统计到达该请求序号的
                session。计划长度包含截止后的尾部。
              </p>
            </>
          )}
          {report && page === "前缀与属性" && (
            <>
              <div className="two-charts">
                {cdfChart(
                  "公共前缀与历史复用",
                  [
                    ["public_prefix_fraction", "公共长度比例"],
                    ["reusable_prefix_fraction", "历史可复用比例"],
                  ],
                  "比例",
                )}
                {cdfChart(
                  "完整 block 重访间隔",
                  [["reuse_interval", "相邻引用时间差"]],
                  "秒",
                )}
              </div>
              <Chart
                title="到达 token 需求"
                domain={domain}
                ylabel="tokens/s"
                logY={logAxis}
                series={[
                  ...timeSeries("input_tokens_per_second", "输入 tokens/s"),
                  {
                    label: "输出 tokens/s",
                    points: timePoints(report, "output_tokens_per_second"),
                    color: palette[1],
                  },
                  ...(old
                    ? [
                        {
                          label: "基线输出 tokens/s",
                          points: timePoints(old, "output_tokens_per_second"),
                          color: palette[1],
                          dash: true,
                        },
                      ]
                    : []),
                ]}
              />
              <Chart
                title="输入长度随时间变化"
                domain={domain}
                ylabel="tokens"
                logY={logAxis}
                series={[
                  ...timeSeries((b) => b.input_quantiles?.[1], "输入 P50"),
                  {
                    label: "输入 P90",
                    points: timePoints(report, (b) => b.input_quantiles?.[2]),
                    color: palette[1],
                  },
                  {
                    label: "输入 P10",
                    points: timePoints(report, (b) => b.input_quantiles?.[0]),
                    color: palette[2],
                  },
                ]}
              />
              <p className="hint">
                完整 block 引用{" "}
                {report.historical_prefix_reuse.block_references} · 历史复用{" "}
                {fmt(report.historical_prefix_reuse.fraction * 100)}
                %。历史机会不等于有限容量缓存命中率；筛选后的复用只基于筛选后的请求历史。
              </p>
            </>
          )}
          <footer>
            <p>{config.notes || "人工参考配置；不是生产测量或校准结果。"}</p>
            {current && (
              <div className="button-row">
                <a
                  className="button"
                  href={`/api/files/${current.run_id}/trace.jsonl`}
                >
                  导出完整 Trace
                </a>
                <a
                  className="button"
                  href={`/api/files/${current.run_id}/trace.jsonl.manifest.json`}
                >
                  导出 Manifest
                </a>
                <button
                  disabled={!report || analyzing}
                  onClick={() =>
                    save("tracegen.report.json", {
                      ...report,
                      run_id: current.run_id,
                      trace_sha256: current.sha256,
                      baseline: baseView
                        ? { config: baseline.config, report: baseView.report }
                        : null,
                    })
                  }
                >
                  导出统计摘要
                </button>
                <button onClick={exportSVG}>导出当前 SVG 图表</button>
              </div>
            )}
            <p className="hint">
              Trace
              导出对应最近成功生成的完整配置；筛选、平滑与放大只改变统计或显示。
            </p>
          </footer>
        </main>
      </div>
    </>
  );
}
createRoot(document.getElementById("root")).render(<App />);
