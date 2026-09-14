import React, { useEffect, useState } from "react";
import { Chart, curvePoints } from "./charts";
export async function api(path, data, signal) {
  const response = await fetch("/api/" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    signal,
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "请求失败");
  return value;
}
export const clone = (x) => structuredClone(x);
export function Field({
  label,
  value,
  onChange,
  min = 0,
  step = "any",
  type = "number",
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        aria-label={label}
        type={type}
        min={min}
        step={step}
        value={value ?? ""}
        onChange={(e) =>
          onChange(
            type === "number"
              ? e.target.value === ""
                ? ""
                : Number(e.target.value)
              : e.target.value,
          )
        }
      />
    </label>
  );
}
export function Select({ label, value, onChange, options }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option
            key={typeof o === "string" ? o : o[0]}
            value={typeof o === "string" ? o : o[0]}
          >
            {typeof o === "string" ? o : o[1]}
          </option>
        ))}
      </select>
    </label>
  );
}
export function JsonEditor({ value, onApply, label = "JSON 配置" }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2)),
    [error, setError] = useState("");
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value]);
  return (
    <div className="json-editor">
      <textarea
        aria-label={label}
        spellCheck="false"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <button
        onClick={async () => {
          try {
            await onApply(JSON.parse(text));
            setError("");
          } catch (e) {
            setError(e.message);
          }
        }}
      >
        应用 {label}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </div>
  );
}
export function CurveEditor({ value, onChange, duration, onFocus }) {
  const points = curvePoints(value, duration),
    spec = typeof value === "number" ? { points } : value;
  const set = (key, v) => {
    const c = { ...spec, [key]: v };
    if (key === "period" && !v) {
      delete c.period;
      delete c.phase;
    }
    onChange(c);
  };
  return (
    <div onFocus={onFocus}>
      <div className="section-title">
        <h3>曲线控制点</h3>
        <button onClick={onFocus}>在主图编辑</button>
      </div>
      <table className="edit-table">
        <thead>
          <tr>
            <th>时间 / s</th>
            <th>值</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {points.map((p, i) => (
            <tr key={i}>
              {p.map((v, j) => (
                <td key={j}>
                  <input
                    aria-label={`控制点 ${i + 1} ${j ? "值" : "时间"}`}
                    type="number"
                    min="0"
                    step="any"
                    value={v}
                    onChange={(e) => {
                      const a = clone(points);
                      a[i][j] = Number(e.target.value);
                      set("points", a);
                    }}
                  />
                </td>
              ))}
              <td>
                <button
                  aria-label={`删除控制点 ${i + 1}`}
                  disabled={points.length <= 1}
                  onClick={() =>
                    set(
                      "points",
                      points.filter((_, k) => i !== k),
                    )
                  }
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        onClick={() => {
          let t = duration / 2;
          const ordered = points.slice().sort((a, b) => a[0] - b[0]);
          let maxGap = -1;
          for (let i = 1; i < ordered.length; i++) {
            const gap = ordered[i][0] - ordered[i - 1][0];
            if (gap > maxGap) {
              maxGap = gap;
              t = (ordered[i][0] + ordered[i - 1][0]) / 2;
            }
          }
          set(
            "points",
            [...points, [t, points[0][1]]].sort((a, b) => a[0] - b[0]),
          );
        }}
      >
        ＋ 添加点
      </button>
      <Select
        label="插值"
        value={spec.interpolation || "linear"}
        options={["linear", "previous"]}
        onChange={(v) => set("interpolation", v)}
      />
      <Field
        label="周期 / s（0 关闭）"
        value={spec.period || 0}
        onChange={(v) => set("period", v)}
      />
      {spec.period > 0 && (
        <Field
          label="相位 / s"
          value={spec.phase || 0}
          onChange={(v) => set("phase", v)}
        />
      )}
    </div>
  );
}
export function ArrivalEditor({ value = {}, onChange }) {
  const kind = value.distribution || "gamma";
  return (
    <>
      <Select
        label="Session 发起分布"
        value={kind}
        options={["gamma", "weibull"]}
        onChange={(v) =>
          onChange(
            v === "gamma"
              ? { distribution: v, cv: 1 }
              : { distribution: v, shape: 1 },
          )
        }
      />
      <Field
        label={kind === "gamma" ? "发起间隔 CV" : "Weibull shape"}
        value={kind === "gamma" ? (value.cv ?? 1) : (value.shape ?? 1)}
        onChange={(v) =>
          onChange({ ...value, [kind === "gamma" ? "cv" : "shape"]: v })
        }
      />
    </>
  );
}
export function BurstEditor({ value = [], onChange, duration, onDragState }) {
  return (
    <div className="burst-editor">
      <h3>Burst</h3>
      {value.map((b, i) => {
        const set = (k, v) => {
          const a = clone(value);
          a[i][k] = v;
          onChange(a);
        };
        return (
          <details key={i} open>
            <summary>Burst {i + 1}</summary>
            <div className="two-fields">
              <Field
                label={`Burst ${i + 1} 开始 / s`}
                value={b.start}
                onChange={(v) => set("start", v)}
              />
              <Field
                label={`Burst ${i + 1} 持续 / s`}
                value={b.duration}
                onChange={(v) => set("duration", v)}
              />
              <Field
                label={`Burst ${i + 1} 倍率`}
                value={b.multiplier ?? 1}
                onChange={(v) => set("multiplier", v)}
              />
              <Field
                label={`Burst ${i + 1} 加量 / sessions/s`}
                value={b.addition ?? 0}
                onChange={(v) => set("addition", v)}
              />
              <Field
                label={`Burst ${i + 1} Ramp / s`}
                value={b.ramp || 0}
                onChange={(v) => set("ramp", v)}
              />
            </div>
            <Chart
              title="拖动起止与倍率"
              height={150}
              domain={[0, duration]}
              series={[
                {
                  label: "倍率",
                  points: [
                    [b.start, 1],
                    [b.start + (b.ramp || 0), b.multiplier ?? 1],
                    [b.start + b.duration - (b.ramp || 0), b.multiplier ?? 1],
                    [b.start + b.duration, 1],
                  ],
                },
              ]}
              handles={[
                [b.start, b.multiplier ?? 1],
                [b.start + b.duration, b.multiplier ?? 1],
              ]}
              onDragState={onDragState}
              onHandle={(index, [x, y]) => {
                const a = clone(value),
                  end = b.start + b.duration;
                if (index === 0) {
                  a[i].start = Math.min(end - 0.01, x);
                  a[i].duration = end - a[i].start;
                } else a[i].duration = Math.max(0.01, x - b.start);
                a[i].multiplier = y;
                a[i].ramp = Math.min(a[i].ramp || 0, a[i].duration / 2);
                onChange(a);
              }}
            />
            <button
              className="danger"
              onClick={() => onChange(value.filter((_, j) => i !== j))}
            >
              删除 Burst {i + 1}
            </button>
          </details>
        );
      })}
      <button
        onClick={() =>
          onChange([
            ...value,
            {
              start: duration * 0.4,
              duration: duration * 0.1,
              multiplier: 2,
              ramp: 0,
            },
          ])
        }
      >
        ＋ 添加 Burst
      </button>
    </div>
  );
}
export const distributionLabels = {
  requests: "请求数 / session",
  initial_private_tokens: "初始私有 tokens",
  growth_multiplier: "Session 增长倍率",
  external_tokens: "逐轮外部新增 tokens",
  output_tokens: "模型输出 tokens",
  gap: "轮间到达间隔 / s",
};
export function DistributionEditor({ field, value, onChange }) {
  const s =
    typeof value === "number"
      ? { distribution: "fixed", value }
      : value || { distribution: "fixed", value: 0 };
  const kind = s.distribution || "fixed";
  const [preview, setPreview] = useState(null),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(
      () =>
        api("distribution", { spec: value, field }, controller.signal)
          .then((v) => {
            setPreview(v);
            setError("");
          })
          .catch((e) => {
            if (e.name !== "AbortError") {
              setError(e.message);
              setPreview(null);
            }
          }),
      250,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [value, field]);
  const set = (k, v) => {
    const out = { ...s, [k]: v };
    if (v === "") delete out[k];
    onChange(out);
  };
  return (
    <details className="distribution">
      <summary>
        {distributionLabels[field]} <small>{kind}</small>
      </summary>
      <Select
        label={`${field} 分布`}
        value={kind}
        options={["fixed", "gamma", "lognormal", "discrete"]}
        onChange={(v) =>
          onChange(
            v === "fixed"
              ? { distribution: v, value: field === "requests" ? 3 : 1 }
              : v === "discrete"
                ? {
                    distribution: v,
                    values: field === "requests" ? [1, 3, 8] : [0, 10, 100],
                    weights: [1, 1, 1],
                  }
                : {
                    distribution: v,
                    mean: field === "requests" ? 5 : 10,
                    cv: 1,
                    ...(field === "requests" ? { min: 1 } : {}),
                  },
          )
        }
      />
      {kind === "fixed" ? (
        <Field
          label={`${field} 固定值`}
          value={s.value}
          onChange={(v) => set("value", v)}
        />
      ) : kind === "discrete" ? (
        <JsonEditor
          label={`${field} values / weights`}
          value={s}
          onApply={onChange}
        />
      ) : (
        <>
          <Field
            label={`${field} mean`}
            value={s.mean}
            onChange={(v) => set("mean", v)}
          />
          <Field
            label={`${field} CV`}
            value={s.cv ?? 1}
            onChange={(v) => set("cv", v)}
          />
          <Field
            label={`${field} min`}
            value={s.min ?? (field === "requests" ? 1 : 0)}
            onChange={(v) => set("min", v)}
          />
          <Field
            label={`${field} max（空为不限）`}
            value={s.max ?? ""}
            onChange={(v) => set("max", v)}
          />
        </>
      )}
      {error && <p className="error">{error}</p>}
      {preview && (
        <>
          <Chart
            title="抽样 CDF"
            height={145}
            xlabel="值"
            ylabel="CDF"
            series={[{ label: "4096 次独立抽样", points: preview.cdf }]}
          />
          <Chart
            title={preview.discrete?"抽样概率质量":"抽样 PDF（分箱密度）"}
            height={145}
            xlabel="值"
            ylabel={preview.discrete?"概率":"概率密度"}
            series={[
              {
                label: "clip / 整数化后",
                points: (preview.discrete?preview.histogram:preview.density).flatMap(([a, b, p]) => [
                  [a, p],
                  [b, p],
                ]),
                step: true,
              },
            ]}
          />
        </>
      )}
    </details>
  );
}

export function Inspector({
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
  onDragState,
}) {
  const duration = config.duration,
    task = config.tasks[taskIndex] || config.tasks[0];
  const update = (fn) => {
    const c = clone(config);
    fn(c);
    setConfig(c);
  };
  const setTask = (k, v) =>
    update((c) => {
      c.tasks[taskIndex][k] = v;
    });
  const clients = task.clients || { count: 1 };
  const explicit = Array.isArray(clients);
  const client = explicit ? clients[clientIndex] || clients[0] : null;
  const setClient = (k, v) => {
    const a = clone(clients);
    a[clientIndex][k] = v;
    setTask("clients", a);
  };
  const focus = (type) => () =>
    setTarget({ type, task: taskIndex, client: clientIndex });
  return (
    <aside className="inspector">
      <nav className="tabs">
        {["整体", "任务", "前缀", "JSON"].map((t) => (
          <button
            className={tab === t ? "selected" : ""}
            key={t}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </nav>
      <div className="inspector-body">
        {tab === "整体" && (
          <>
            <Field
              label="时长 / s"
              value={duration}
              onChange={(v) => update((c) => (c.duration = v))}
            />
            <Field
              label="Seed"
              value={config.seed || 0}
              step="1"
              onChange={(v) => update((c) => (c.seed = v))}
            />
            <Field
              label="Block / tokens"
              value={config.block_size}
              step="1"
              onChange={(v) => update((c) => (c.block_size = v))}
            />
            <h3>发起曲线</h3>
            <CurveEditor
              value={config.traffic.session_rate}
              duration={duration}
              onChange={(v) => update((c) => (c.traffic.session_rate = v))}
              onFocus={() => setTarget({ type: "global" })}
            />
            <details>
              <summary>时钟与输出</summary>
              <Field
                label="积分网格 / s"
                value={config.traffic.resolution ?? 1}
                onChange={(v) => update((c) => (c.traffic.resolution = v))}
              />
              <ArrivalEditor
                value={config.traffic.arrival}
                onChange={(v) => update((c) => (c.traffic.arrival = v))}
              />
              <label className="check">
                <input
                  type="checkbox"
                  checked={config.output?.request_metadata ?? true}
                  onChange={(e) =>
                    update(
                      (c) =>
                        (c.output = { request_metadata: e.target.checked }),
                    )
                  }
                />
                输出请求长度 metadata
              </label>
            </details>
            <BurstEditor
              value={config.traffic.bursts}
              onChange={(v) => update((c) => (c.traffic.bursts = v))}
              duration={duration}
              onDragState={onDragState}
            />
          </>
        )}
        {tab === "任务" && (
          <>
            <Select
              label="编辑 task"
              value={taskIndex}
              options={config.tasks.map((t, i) => [i, t.key])}
              onChange={(v) => {
                setTaskIndex(Number(v));
                setClientIndex(0);
                setTarget({ type: "task", task: Number(v) });
              }}
            />
            <div className="button-row">
              <button
                onClick={() =>
                  update((c) => {
                    let key = "task-" + c.tasks.length;
                    while (c.tasks.some((t) => t.key === key)) key += "x";
                    c.tasks.push({ ...clone(task), key });
                  })
                }
              >
                ＋ 任务
              </button>
              <button
                disabled={config.tasks.length <= 1}
                onClick={() => {
                  update((c) => c.tasks.splice(taskIndex, 1));
                  setTaskIndex(0);
                  setClientIndex(0);
                  setTarget({ type: "global" });
                }}
              >
                删除任务
              </button>
            </div>
            <Field
              label="Task key"
              type="text"
              value={task.key}
              onChange={(v) => setTask("key", v)}
            />
            <details open>
              <summary>任务权重</summary>
              <CurveEditor
                value={task.weight ?? 1}
                duration={duration}
                onChange={(v) => setTask("weight", v)}
                onFocus={focus("task")}
              />
            </details>
            <details open>
              <summary>Client pool</summary>
              <Select
                label="Client 配置方式"
                value={explicit ? "explicit" : "count"}
                options={[
                  ["count", "数量与集中度"],
                  ["explicit", "显式 client"],
                ]}
                onChange={(v) => {
                  setClientIndex(0);
                  setTarget({ type: "global" });
                  setTask(
                    "clients",
                    v === "count"
                      ? { count: explicit ? clients.length : 1 }
                      : Array.from({ length: clients.count || 1 }, (_, i) => ({
                          key: `client-${String(i).padStart(6, "0")}`,
                          weight: 1 / (i + 1) ** (clients.weight_exponent || 0),
                        })),
                  );
                }}
              />
              {!explicit ? (
                <>
                  <Field
                    label="Client 数量"
                    value={clients.count}
                    step="1"
                    onChange={(v) =>
                      setTask("clients", { ...clients, count: v })
                    }
                  />
                  <Field
                    label="头部集中指数"
                    value={clients.weight_exponent || 0}
                    onChange={(v) =>
                      setTask("clients", { ...clients, weight_exponent: v })
                    }
                  />
                </>
              ) : (
                <>
                  <Select
                    label="编辑 client"
                    value={clientIndex}
                    options={clients.map((c, i) => [i, c.key])}
                    onChange={(v) => {
                      setClientIndex(Number(v));
                      setTarget({
                        type: "client",
                        task: taskIndex,
                        client: Number(v),
                      });
                    }}
                  />
                  <div className="button-row">
                    <button
                      onClick={() => {
                        let key = "client-" + clients.length;
                        while (clients.some((c) => c.key === key)) key += "x";
                        setTask("clients", [...clients, { key, weight: 1 }]);
                      }}
                    >
                      ＋ Client
                    </button>
                    <button
                      disabled={clients.length <= 1}
                      onClick={() => {
                        setTask(
                          "clients",
                          clients.filter((_, i) => i !== clientIndex),
                        );
                        setClientIndex(0);
                        setTarget({ type: "global" });
                      }}
                    >
                      删除 Client
                    </button>
                  </div>
                  <Field
                    label="Client key"
                    value={client.key}
                    type="text"
                    onChange={(v) => setClient("key", v)}
                  />
                  <h4>Client 权重</h4>
                  <CurveEditor
                    value={client.weight ?? 1}
                    duration={duration}
                    onChange={(v) => setClient("weight", v)}
                    onFocus={focus("client")}
                  />
                  <h4>活跃倍率</h4>
                  <CurveEditor
                    value={client.activity ?? 1}
                    duration={duration}
                    onChange={(v) => setClient("activity", v)}
                    onFocus={focus("activity")}
                  />
                  <ArrivalEditor
                    value={
                      client.arrival || task.arrival || config.traffic.arrival
                    }
                    onChange={(v) => setClient("arrival", v)}
                  />
                  <BurstEditor
                    value={client.bursts}
                    onChange={(v) => setClient("bursts", v)}
                    duration={duration}
                    onDragState={onDragState}
                  />
                </>
              )}
            </details>
            <details>
              <summary>任务发起时钟</summary>
              <ArrivalEditor
                value={task.arrival || config.traffic.arrival}
                onChange={(v) => setTask("arrival", v)}
              />
            </details>
            <h3>Session 行为分布</h3>
            {Object.keys(distributionLabels).map((field) => (
              <DistributionEditor
                key={field}
                field={field}
                value={
                  task.session[field] ?? (field === "growth_multiplier" ? 1 : 0)
                }
                onChange={(v) =>
                  setTask("session", { ...task.session, [field]: v })
                }
              />
            ))}
            <h3>公共组热度</h3>
            {(config.prefix_groups || []).map((g) => {
              const choice = (task.prefix_groups || []).find(
                (p) => p.key === g.key,
              );
              return (
                <div className="group-choice" key={g.key}>
                  <label>
                    <input
                      type="checkbox"
                      checked={!!choice}
                      onChange={(e) =>
                        setTask(
                          "prefix_groups",
                          e.target.checked
                            ? [
                                ...(task.prefix_groups || []),
                                { key: g.key, weight: 1 },
                              ]
                            : (task.prefix_groups || []).filter(
                                (p) => p.key !== g.key,
                              ),
                        )
                      }
                    />
                    {g.key}
                  </label>
                  {choice && (
                    <Field
                      label={`${g.key} 权重`}
                      value={choice.weight ?? 1}
                      onChange={(v) =>
                        setTask(
                          "prefix_groups",
                          task.prefix_groups.map((p) =>
                            p.key === g.key ? { ...p, weight: v } : p,
                          ),
                        )
                      }
                    />
                  )}
                </div>
              );
            })}
            <BurstEditor
              value={task.bursts}
              onChange={(v) => setTask("bursts", v)}
              duration={duration}
              onDragState={onDragState}
            />
          </>
        )}
        {tab === "前缀" && (
          <>
            <p className="hint">公共组长度固定；私有后缀按 session 隔离。</p>
            {(config.prefix_groups || []).map((g, i) => {
              const set = (k, v) =>
                update((c) => {
                  c.prefix_groups[i][k] = v;
                  if (k === "key")
                    for (const t of c.tasks)
                      for (const p of t.prefix_groups || [])
                        if (p.key === g.key) p.key = v;
                });
              return (
                <details open key={i}>
                  <summary>公共组 {i + 1}</summary>
                  <Field
                    label={`组 ${i + 1} key`}
                    type="text"
                    value={g.key}
                    onChange={(v) => set("key", v)}
                  />
                  <Field
                    label={`组 ${i + 1} tokens`}
                    value={g.tokens}
                    onChange={(v) => set("tokens", v)}
                  />
                  <Select
                    label={`组 ${i + 1} scope`}
                    value={g.scope || "task"}
                    options={["global", "task", "client"]}
                    onChange={(v) => set("scope", v)}
                  />
                  <button
                    onClick={() =>
                      update((c) => {
                        c.prefix_groups.splice(i, 1);
                        for (const t of c.tasks)
                          t.prefix_groups = (t.prefix_groups || []).filter(
                            (p) => p.key !== g.key,
                          );
                      })
                    }
                  >
                    删除公共组
                  </button>
                </details>
              );
            })}
            <button
              onClick={() =>
                update((c) => {
                  c.prefix_groups ??= [];
                  let key = "group-" + c.prefix_groups.length;
                  while (c.prefix_groups.some((g) => g.key === key)) key += "x";
                  c.prefix_groups.push({ key, tokens: 512, scope: "task" });
                })
              }
            >
              ＋ 公共组
            </button>
          </>
        )}
        {tab === "JSON" && (
          <>
            <p className="hint">
              编辑完整配置；应用后自动生成。参数说明见仓库
              docs/configuration.md。
            </p>
            <JsonEditor
              value={config}
              label="完整配置"
              onApply={async (v) => {
                await api("validate", { config: v });
                if (!v.tasks?.length || !v.traffic)
                  throw Error("需要 tasks 与 traffic");
                setConfig(v);
                setTaskIndex(0);
                setClientIndex(0);
                setTarget({ type: "global" });
              }}
            />
          </>
        )}
      </div>
    </aside>
  );
}
