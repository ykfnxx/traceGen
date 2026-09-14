import React, { useRef, useState, useEffect } from "react";
export const palette = [
  "#039c9f",
  "#699bed",
  "#83bd7e",
  "#ae8ad4",
  "#d49a54",
  "#cb7596",
];
export const fmt = (v) =>
  v == null
    ? "—"
    : Math.abs(v) >= 10000
      ? `${(v / 1000).toFixed(1)}k`
      : Number(v.toPrecision(3)).toString();
const valid = (p) => p && Number.isFinite(p[0]) && Number.isFinite(p[1]);
const log = (v) => Math.sign(v) * Math.log1p(Math.abs(v));
const unlog = (v) => Math.sign(v) * Math.expm1(Math.abs(v));

export function Chart({
  title,
  series = [],
  band,
  stack = [],
  density = [],
  handles,
  onHandle,
  onDragState,
  domain,
  logX = false,
  logY = false,
  xlabel = "时间 / s",
  ylabel = "",
  height = 220,
  onBrush,
  testId,
}) {
  const ref = useRef(null),
    drag = useRef(null),
    [hover, setHover] = useState(null),
    [brush, setBrush] = useState(null);
  const [width, setWidth] = useState(900);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(240, entry.contentRect.width)),
    );
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const W = width,
    H = height,
    L = 62,
    R = 18,
    T = 14,
    B = 38;
  const all = series
    .flatMap((s) => s.points || [])
    .concat(
      stack.flatMap((s) => s.points || []),
      handles || [],
      band?.upper || [],
      density.flatMap((d) => [
        [d.gap_low, d.tokens_low],
        [d.gap_high, d.tokens_high],
      ]),
    )
    .filter(valid);
  const minX = domain?.[0] ?? all.reduce((a, p) => Math.min(a, p[0]), 0);
  const maxX = domain?.[1] ?? all.reduce((a, p) => Math.max(a, p[0]), 1);
  let maxY = all.reduce((a, p) => Math.max(a, p[1]), 1e-6);
  if (stack.length)
    maxY = Math.max(
      maxY,
      ...(stack[0].points || []).map((_, i) =>
        stack.reduce((a, s) => a + (s.points[i]?.[1] || 0), 0),
      ),
    );
  maxY *= handles ? 1.25 : 1.08;
  maxY = drag.current?.maxY ?? maxY;
  const minY = all.reduce((a, p) => Math.min(a, p[1]), 0);
  const tx = logX ? log : (v) => v,
    ty = logY ? log : (v) => v,
    ix = logX ? unlog : (v) => v,
    iy = logY ? unlog : (v) => v;
  const sx = (v) =>
    L + ((tx(v) - tx(minX)) / (tx(maxX) - tx(minX) || 1)) * (W - L - R);
  const sy = (v) =>
    H - B - ((ty(v) - ty(minY)) / (ty(maxY) - ty(minY) || 1)) * (H - T - B);
  const fromEvent = (e) => {
    const r = ref.current.getBoundingClientRect();
    return [
      ((e.clientX - r.left) / r.width) * W,
      ((e.clientY - r.top) / r.height) * H,
    ];
  };
  const unsx = (v) =>
    ix(tx(minX) + ((v - L) / (W - L - R)) * (tx(maxX) - tx(minX)));
  const unsy = (v) =>
    iy(ty(minY) + ((H - B - v) / (H - T - B)) * (ty(maxY) - ty(minY)));
  const path = (points, step = false) => {
    let connected = false;
    return points
      .map((p) => {
        if (!valid(p)) {
          connected = false;
          return "";
        }
        const command = !connected
          ? `M${sx(p[0])},${sy(p[1])}`
          : step === "reverse"
            ? `V${sy(p[1])}H${sx(p[0])}`
            : step
              ? `H${sx(p[0])}V${sy(p[1])}`
              : `L${sx(p[0])},${sy(p[1])}`;
        connected = true;
        return command;
      })
      .join(" ");
  };
  const onMove = (e) => {
    const [x, y] = fromEvent(e);
    if (drag.current) {
      onHandle?.(drag.current.index, [
        Math.max(minX, Math.min(maxX, unsx(x))),
        Math.max(0, unsy(Math.max(T, Math.min(H - B, y)))),
      ]);
      return;
    }
    if (brush) {
      setBrush([brush[0], unsx(x)]);
      return;
    }
    setHover(unsx(x));
  };
  const end = (e) => {
    if (drag.current) {
      drag.current = null;
      onDragState?.(false);
    }
    if (brush) {
      if (Math.abs(brush[1] - brush[0]) > (maxX - minX) * 0.02)
        onBrush?.(
          brush
            .slice()
            .sort((a, b) => a - b)
            .map((x) => Math.max(minX, Math.min(maxX, x))),
        );
      setBrush(null);
    }
  };
  const id = React.useId().replaceAll(":", "");
  return (
    <section className="chart" data-testid={testId}>
      <div className="chart-heading">
        <h3>{title}</h3>
        <div className="legend">
          {[
            ...series,
            ...stack.map((s, i) => ({
              ...s,
              color: palette[(i + 1) % palette.length],
            })),
          ].map((s, i) => (
            <span key={i}>
              <i
                style={{
                  background: s.color || palette[i % palette.length],
                  opacity: s.dash ? 0.7 : 1,
                }}
              />
              {s.label}
            </span>
          ))}
        </div>
      </div>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={title}
        onPointerMove={onMove}
        onPointerUp={end}
        onPointerCancel={end}
        onPointerLeave={() => setHover(null)}
      >
        <defs>
          <clipPath id={id}>
            <rect x={L} y={T} width={W - L - R} height={H - T - B} />
          </clipPath>
        </defs>
        {[0, 1, 2, 3, 4].map((i) => {
          const v = iy(ty(minY) + ((ty(maxY) - ty(minY)) * i) / 4);
          return (
            <g key={i}>
              <line className="grid" x1={L} x2={W - R} y1={sy(v)} y2={sy(v)} />
              <text x={L - 8} y={sy(v) + 4} textAnchor="end">
                {fmt(v)}
              </text>
            </g>
          );
        })}
        {[0, 1, 2, 3, 4, 5, 6].map((i) => {
          const v = ix(tx(minX) + ((tx(maxX) - tx(minX)) * i) / 6);
          return (
            <g key={i}>
              <line className="grid" x1={sx(v)} x2={sx(v)} y1={T} y2={H - B} />
              <text x={sx(v)} y={H - B + 19} textAnchor="middle">
                {fmt(v)}
              </text>
            </g>
          );
        })}
        <text x={W / 2} y={H - 3} textAnchor="middle">
          {xlabel}
        </text>
        <text
          transform={`translate(14 ${H / 2}) rotate(-90)`}
          textAnchor="middle"
        >
          {ylabel}
        </text>
        <g clipPath={`url(#${id})`}>
          {band && (
            <path
              d={
                path(band.upper) +
                path([...band.lower].reverse()).replace(/^M/, "L") +
                "Z"
              }
              fill="#d5ebf7"
              opacity=".85"
            />
          )}
          {stack.map((s, i) => {
            const lower = s.points.map((p, k) => [
              p[0],
              stack
                .slice(0, i)
                .reduce((n, a) => n + (a.points[k]?.[1] || 0), 0),
            ]);
            const upper = s.points.map((p, k) => [p[0], lower[k][1] + p[1]]);
            return (
              <path
                key={s.label}
                d={
                  path(upper, true) +
                  path([...lower].reverse(), "reverse").replace(/^M/, "L") +
                  "Z"
                }
                fill={palette[(i + 1) % palette.length]}
                opacity=".38"
              />
            );
          })}
          {density.map((d, i) => (
            <rect
              key={i}
              x={sx(d.gap_low)}
              y={sy(d.tokens_high)}
              width={Math.max(0.5, sx(d.gap_high) - sx(d.gap_low))}
              height={Math.max(0.5, sy(d.tokens_low) - sy(d.tokens_high))}
              fill="#008e98"
              opacity={
                0.15 +
                (0.85 * Math.log1p(d.count)) /
                  Math.log1p(Math.max(...density.map((x) => x.count)))
              }
            >
              <title>{d.count} requests</title>
            </rect>
          ))}
          {series.map((s, i) => (
            <path
              key={i}
              d={path(s.points || [], s.step)}
              fill="none"
              stroke={s.color || palette[i % palette.length]}
              strokeWidth="2"
              strokeDasharray={s.dash ? "6 4" : undefined}
            />
          ))}
          {onBrush && (
            <rect
              x={L}
              y={T}
              width={W - L - R}
              height={H - T - B}
              fill="transparent"
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                const v = unsx(fromEvent(e)[0]);
                setBrush([v, v]);
              }}
            />
          )}
          {brush && (
            <rect
              x={sx(Math.min(...brush))}
              y={T}
              width={Math.abs(sx(brush[1]) - sx(brush[0]))}
              height={H - T - B}
              fill="#039c9f"
              opacity=".13"
              pointerEvents="none"
            />
          )}
          {hover != null && !drag.current && (
            <line
              x1={sx(hover)}
              x2={sx(hover)}
              y1={T}
              y2={H - B}
              stroke="#9aa8b9"
              strokeDasharray="3 3"
              pointerEvents="none"
            />
          )}
        </g>
        {handles?.map((p, i) =>
          p[0] < minX || p[0] > maxX ? null : (
            <circle
              className="handle"
              data-testid={`handle-${i}`}
              key={i}
              cx={sx(p[0])}
              cy={sy(p[1])}
              r="5"
              tabIndex="0"
              aria-label={`控制点 ${i + 1}`}
              onPointerDown={(e) => {
                e.stopPropagation();
                e.currentTarget.setPointerCapture(e.pointerId);
                drag.current = { index: i, maxY };
                onDragState?.(true);
              }}
              onKeyDown={(e) => {
                const a = [
                  "ArrowLeft",
                  "ArrowRight",
                  "ArrowUp",
                  "ArrowDown",
                ].indexOf(e.key);
                if (a >= 0) {
                  e.preventDefault();
                  onHandle(i, [
                    Math.max(0, p[0] + (a === 0 ? -1 : a === 1 ? 1 : 0)),
                    Math.max(
                      0,
                      p[1] + ((a === 2 ? 1 : a === 3 ? -1 : 0) * maxY) / 100,
                    ),
                  ]);
                }
              }}
            />
          ),
        )}
      </svg>
      {hover != null && (
        <div className="tooltip">
          {xlabel}: {fmt(hover)}{" "}
          {series.map((s, i) => {
            const points = s.points?.filter(valid) || [];
            const nearest = points.reduce(
              (a, p) =>
                !a || Math.abs(p[0] - hover) < Math.abs(a[0] - hover) ? p : a,
              null,
            );
            return (
              <span key={i}>
                {s.label}: {fmt(nearest?.[1])}
              </span>
            );
          })}
        </div>
      )}
      {!all.length && <p className="empty">当前选择没有可显示的数据</p>}
    </section>
  );
}

export function curvePoints(spec, duration) {
  if (typeof spec === "number")
    return [
      [0, spec],
      [duration, spec],
    ];
  return (
    spec?.points || [
      [0, 0],
      [duration, 0],
    ]
  );
}
export function curveValue(spec, t) {
  if (typeof spec === "number") return spec;
  const p = spec?.points || [[0, 0]];
  if (spec.period)
    t = (((t - (spec.phase || 0)) % spec.period) + spec.period) % spec.period;
  if (t <= p[0][0]) return p[0][1];
  for (let i = 1; i < p.length; i++) {
    if (t < p[i][0])
      return spec.interpolation === "previous"
        ? p[i - 1][1]
        : p[i - 1][1] +
            ((p[i][1] - p[i - 1][1]) * (t - p[i - 1][0])) /
              (p[i][0] - p[i - 1][0]);
  }
  return p.at(-1)[1];
}
export function sampleCurve(spec, duration) {
  const xs = new Set(
    Array.from({ length: 301 }, (_, i) => (i * duration) / 300),
  );
  curvePoints(spec, duration).forEach((p) => xs.add(p[0]));
  return [...xs]
    .filter((x) => x <= duration)
    .sort((a, b) => a - b)
    .map((x) => [x, curveValue(spec, x)]);
}
export function smooth(points, n) {
  return points.map((p, i) => [
    p[0],
    p[1] == null
      ? null
      : points
          .slice(Math.max(0, i - n + 1), i + 1)
          .reduce((a, v) => a + (v[1] || 0), 0) / Math.min(i + 1, n),
  ]);
}
