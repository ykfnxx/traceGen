# 配置驱动生成协议（version 3）

本协议由 `tracegen.clientpool.ClientPool` 验证，`tracegen.synthesis` 执行。`generate.py` 与交互工作台只使用此路径。未知字段直接报错，不能混入旧 `datasets`、`request_ratio`、`calibration`、`new_block_jitter` 或 `max_concurrent_sessions` 参数。

所有时间单位为秒，长度单位为 token，强度单位为 sessions/s。数值必须有限；seed、计数与固定 token 长度使用非负整数。下表中的必填字段没有默认值。

## 顶层

| 字段 | 默认值 | 含义 |
|---|---|---|
| `version` | 必填，`3` | 配置版本 |
| `duration` | 必填 | 正数，输出窗口 `[0, duration)` |
| `seed` | `0` | 非负整数 |
| `block_size` | 必填 | 正整数，完整合成 block 的 token 数 |
| `traffic` | 必填 | 整体发起强度与默认到达分布 |
| `tasks` | 必填 | 非空任务数组，`key` 唯一 |
| `prefix_groups` | `[]` | 公共前缀组注册表 |
| `output.request_metadata` | `true` | 是否在请求中保留长度与请求序号 |
| `notes` | 无 | 人工说明和参数来源；不参与生成 |

`traffic` 字段：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `session_rate` | 必填 | 非负常数或曲线 |
| `resolution` | `1` | 正数，数值积分的最大时间网格宽度 |
| `arrival` | Gamma，CV=1 | client 发起时钟的默认分布 |
| `bursts` | `[]` | 全局 burst 数组 |

## 曲线

强度、task/client 权重及 client 活跃倍率均可写为非负常数或对象：

```json
{"points": [[0, 0.1], [60, 0.5], [120, 0.1]], "interpolation": "linear", "period": 120, "phase": 0}
```

| 字段 | 默认值 | 含义 |
|---|---|---|
| `points` | 必填 | 非空 `[time, value]` 数组，时间非负且严格递增，值非负 |
| `interpolation` | `linear` | `linear` 线性，或 `previous` 左值保持 |
| `period` | 无 | 正数；有周期时控制点须从 0 开始且不超过周期 |
| `phase` | `0` | 非负右移量；非零值要求配置 period |

非周期曲线在首尾以端点值延伸。周期曲线计算 `(t - phase) % period`，周期边界可以有跳变；若需连续周期，首尾值应一致。

生成器把固定 `resolution` 网格与所有曲线拐点、周期边界和 burst 边界合并，每段使用中点有效强度。它是明确的数值近似，不保证原连续曲线的精确积分；线性权重归一化和多个倍率相乘后仍可能有曲率。减小 `resolution` 可检查收敛，零流量区与显式跳变不会被跨界平均。改变网格可能改变绝对发起时间。

## Task 与 client

`tasks[]`：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `key` | 必填 | 稳定非空字符串；参与身份与随机种子 |
| `weight` | `1` | 任务基础 session 份额的归一化权重，可为曲线 |
| `clients` | `{"count": 1}` | 自动创建或显式列出 client |
| `arrival` | 继承 traffic | 当前任务内 client 的默认发起分布 |
| `bursts` | `[]` | 任务分配后额外作用的 burst |
| `prefix_groups` | `[]` | 非空时每个 session 选择一个组：`{"key": "system", "weight": 1}` |
| `session` | 必填 | 任务行为分布 |
| `notes` | 无 | 参数依据，不参与生成 |

组选择权重非负，总和必须大于零；省略组选择数组表示没有公共前缀。

自动 client 配置：`{"count": 8, "weight_exponent": 1.2}`。数量为正整数；指数为非负数，默认 0。client key 为 `client-000000` 等，排名 r 从 1 开始，权重为 `1 / r^weight_exponent`。数量增加会改变归一化份额，但已有 key 不变。

显式 client 示例：

```json
[
  {"key": "head", "weight": 4, "activity": 1},
  {"key": "worker", "weight": 1,
   "activity": {"points": [[0, 0], [30, 1]], "interpolation": "previous"},
   "bursts": [{"start": 60, "duration": 20, "multiplier": 3}]}
]
```

显式字段为 `key`（任务内唯一）、`weight`（默认 1，常数/曲线）、`activity`（默认 1，常数/曲线）、`bursts`（默认空）、`arrival`（继承 task）。非空数组。

计算顺序：

```text
base = global_burst(S(t))
allocated_task = base × task_weight / sum(task_weights)
effective_task = task_burst(allocated_task)
allocated_client = effective_task × client_weight / sum(weights_in_task)
effective_client = client_burst(allocated_client × activity)
```

分母为零时该层基础分配为零；显式加量 burst 仍可产生流量。`weight` 调整固定基础总量的组成；`activity` 与局部 burst 改变总量。`activity=0` 不把停用 client 的份额补给其他 client。任务数、client 数、实际 session 占比与最终请求占比不是同一量。

## Burst

全局、任务和 client 都支持相同结构：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `start` | 必填 | 非负起点 |
| `duration` | 必填 | 正长度，整段须位于输出时长内 |
| `multiplier` | `1` | 非负倍率；小于 1 可形成波谷 |
| `addition` | `0` | 非负绝对 sessions/s 加量；任务层加量先分配到 client |
| `ramp` | `0` | 对称上升、下降各自的秒数，不能超过 duration/2 |

无 ramp 时作用范围 `[start, start + duration)`。有 ramp 时强度从 0 线性升到 1，再线性回到 0。对每个活跃 burst 的 ramp 强度 a，倍率为 `1 + (multiplier - 1) × a`，加量为 `addition × a`。重叠 burst 的倍率相乘、加量相加，最终 `rate × product(multipliers) + sum(additions)`。

Burst 只影响新 session 发起。Session 内请求到达时间不因此压缩、丢弃或排队。

## 发起时钟

`arrival` 支持：

- `{"distribution": "gamma", "cv": 1}`：默认形式；CV 非负，0 为单位积分步长，1 为指数间隔，更大值增加随机间隔波动。
- `{"distribution": "weibull", "shape": 0.7}`：shape 正数，默认 1。

时钟在累计强度空间抽样单位均值间隔，再反解物理到达时间；跨曲线分段和零强度区保留残量，不重新抽样。首次发起也需要消耗第一次间隔，不强制在 t=0 创建 session。每个 client 独立运行，因此固定间隔的多个 client 可能同步。允许浮点分辨率导致的同时到达。

CV 是每个 client 的积分时钟间隔 CV，不是服务端总 IAT 或窗口请求计数 CV。

## Session 行为分布

`tasks[].session`：

| 字段 | 默认值 | 采样频次和单位 |
|---|---|---|
| `requests` | 必填 | 每个 session 一次，LLM 请求数，整数 >=1 |
| `initial_private_tokens` | 必填 | 每个 session 一次，初始私有 token 数 |
| `growth_multiplier` | `1` | 每个 session 一次，非负倍率 |
| `external_tokens` | `0` | 后续每次请求，外部新 token 数（不含模型输出） |
| `output_tokens` | `0` | 每次请求，模型输出 token 数 |
| `gap` | 必填 | 后续每次请求，相对上次的到达间隔，非负秒数 |

支持四种分布：

| 写法 | 参数化 |
|---|---|
| 数值，或 `{"distribution":"fixed","value":128}` | 固定值 |
| `{"distribution":"gamma","mean":100,"cv":0.8,"min":0,"max":1000}` | 算术均值；shape=`1/cv²`，scale=`mean×cv²` |
| `{"distribution":"lognormal","mean":100,"cv":0.8,"min":0,"max":1000}` | 算术均值；sigma²=`log(1+cv²)`，mu=`log(mean)-sigma²/2` |
| `{"distribution":"discrete","values":[1,3,8],"weights":[0.2,0.6,0.2]}` | values 非空，weights 默认均匀；非负且总和大于零 |

Gamma/Lognormal 的 mean 必须为正，CV 默认 1，CV=0 退化为 mean。`min/max` 仅适用于这两类分布，采用 **clip** 而非拒绝重采样；默认 min 为 0（requests 为 1），max 不限。因此配置 mean/CV 描述 clip 和整数化之前的分布，不保证最终样本保留相同均值/CV。

请求数、初始/外部/输出 token 数采用 `floor(x+0.5)` 整数化；它们的 fixed/discrete 值及 min/max 必须是整数。Growth multiplier 和 gap 保留浮点。外部 token 样本先整数化，再乘 session 增长倍率并再次 `floor(x+0.5)`。

```text
L0 = public_tokens + initial_private_tokens
Lk = L(k-1) + output(k-1) + round_half_up(growth_multiplier × external_sample(k))
blocks(k) = floor(Lk / block_size)
```

增长倍率不影响初始长度或模型输出。末轮 output 记录为本次模型生成长度，但不会进入任何后续输入。

## 前缀组与种子

顶层 `prefix_groups[]` 包含 `key`（全局唯一）、`tokens`（非负整数）、`scope`（默认 task，可为 global/task/client）。同一组在其 scope 内共享确定身份。仅完整公共 block 可跨 session 复用，跨越公共尾部与私有内容的 block 属于私有部分。

Block ID 是 128-bit 十六进制字符串，使用含父 hash 的稳定摘要。公共身份包含组 key、长度、scope、作用域身份与 block 粒度；私有身份额外含 session ID/seed。它们不是任何 serving 框架的原生 hash ABI，不表示真实 token 内容。

种子算法是 UTF-8 编码的紧凑 JSON 数组，经 SHA-256 取前 128 bit：

```text
session_seed = stable_seed(global_seed, task_key, client_key, ordinal, "session-v1")
substream_seed = stable_seed(session_seed, "structure" | "growth" | "output" | "timing")
arrival_seed = stable_seed(global_seed, task_key, client_key, "arrival-v1")
```

`structure` 抽取请求数、初始长度、增长倍率和公共组；其他三个流分别抽取外部新增、输出和间隔。Session 序号是 client 本地从 0 开始的发起序号，种子不含绝对时间。Task/client key 用百分号编码后与序号组成输出 session ID；manifest 记录原始 key 和十进制 seed 字符串。

固定生成器/Python 随机实现、profile、seed、组配置时，对应身份的 session 相对轨迹保持不变。增删其他任务可能改变归一化率、开始时间、输出集合和截断，但不改变对应 session 的随机实现。更改分布类型可能改变本子流后续随机消耗，这是 profile 变化的一部分。

## CLI 与统计

`generate.py --config ... --output ...` 可覆盖 `--duration`、`--block-size`、`--seed`；`--session-rate` 替换整体曲线为常数，`--arrival-cv` 替换 traffic 默认为 Gamma（task/client 覆盖仍有效）。不接受旧版数据集与校准参数。

`experiments/run_synthetic.py --config ... --output-dir ... --window 10 [--no-plots]` 执行同一合成核心，再生成统计。窗口必须为正，最后不足一个窗口的 RPS 用实际窗口长度归一化。报告 CDF 采用 101 个分位点表示；自相关使用完整窗口的请求计数，零方差返回 null。上下文分位数仅包含窗口内到达该请求序号的 session。

计划 session 规模/时长统计包含截止后的尾部；observed/emitted 统计仅包含窗口内请求。报告中外部新增分布与间隔—增长联合统计不包含首请求。复用间隔按曾出现的完整 block、相邻引用时间差统计，属于 block 引用加权分布。重复前缀统计不建模驱逐、容量或 TTL。

关闭请求 metadata 时仍计算到达和 block 复用统计，token/请求序号图不可用；不从 block 数猜测完整 token 长度。图表设置不进入生成配置、不消耗生成随机流。

当前无自动拟合、在线 serving 反馈、最大上下文截断或压缩。长 session 可以持续增长；通过配置轮数及增量分布的上界控制实验规模。


## 交互编辑与导出

`preview.py` 启动本地工作台，构建步骤和使用方法见[工作台说明](workbench.md)。每份完整配置对应独立运行 ID；相同配置在一次服务进程中复用已生成 trace。窗口、task/client 筛选不属于生成配置，重新统计已有文件。筛选后 session、RPS、活跃数量、属性和历史复用全部按选中来源计算，历史复用不借用被过滤来源。

全局曲线编辑图显示原配置曲线，不含 burst；结果区另列经过 burst、权重及 activity 分配后的有效强度。任务权重与 client 权重/活跃曲线可切换到主图编辑。周期控制点在一个周期内编辑，主图显示整个时长内的周期展开。

Session 分布预览使用独立固定种子的 4096 次抽样，展示 clip/整数化后的 CDF 和抽样 PDF/概率质量，PDF 使用归一化分箱密度估计，不是解析 PDF。分布控件、来源筛选与图表缩放不会消费生成器随机数。

基线保留运行 ID、原配置与原 seed。当前配置改动后不会覆盖基线；选择相同的统计窗口和来源重新统计两份 trace 后叠加。当前 seed 可显式修改，界面同时标注基线与当前 seed。
