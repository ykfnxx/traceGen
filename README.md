# traceGen

按照参考数据集的完整 session 模板，合成用于 KVCache 管理回放的请求到达 trace：

```json
{"timestamp":5.85,"hash_ids":[123,456],"session_id":"s00000000"}
```

只关心请求到达，默认请求都能处理；不模拟服务耗时、请求完成、服务端队列或算力容量。输出按时间排序，`timestamp` 的单位为秒，`hash_ids` 是完整 KV block 的有序 uint64 前缀标识。

## 运行

生成器仅依赖 Python 标准库（Python 3.10+），无需安装 ServeGen。先用仓库内置的小型示例验证运行环境：

```bash
git clone git@github.com:ykfnxx/traceGen.git
cd traceGen
python3 generate.py --config examples/demo.json --output runs/demo_v2.jsonl
```

使用真实 Weka 数据时，先把 `examples/weka.json` 中 `datasets[0].path` 改为本机原始 `traces.jsonl` 的路径。然后生成 3 小时 trace：

```bash
python3 generate.py --config examples/weka.json --output runs/weka_v2.jsonl \
  --duration 10800 --block-size 128 --seed 42 \
  --max-concurrent-sessions 32 --session-rate 0.01 --arrival-cv 1.5
```

配置中的相对数据路径以配置文件所在目录为基准；输出路径以当前目录为基准。`.jsonl.gz` 输出启用 gzip。每份 trace 附带 `.manifest.json`，记录输入指纹、session 来源、实际并发、流量和截止统计。

上面的 Weka 示例配置包含第 60–70 分钟的 burst。需要关闭时，将配置中的 `bursts` 改为 `[]`。调整 `duration` 后，也要确保所有 burst 窗口都在新的时长内。

CLI 可覆盖配置中的 `--duration`、`--block-size`、`--seed`、`--max-concurrent-sessions`、`--session-rate`；`--arrival-cv` 会选择 Gamma 分布并设置其 CV。burst 时间段和数据源混合通过 JSON 配置调整。完整参数可用 `python3 generate.py --help` 查看。

生成后，终端会输出统计、trace 和 manifest 的路径。可以直接读取 manifest 查看峰值并发与实际请求量：

```bash
python3 - <<'PY'
import json
from pathlib import Path
manifest = json.loads(Path('runs/demo_v2.jsonl.manifest.json').read_text())
print(json.dumps(manifest['stats'], indent=2))
PY
```

## v2 参数

**旧的 `load_scale` 时间缩放定义已废弃。** 配置出现 `load_scale` 或 `base_session_rate` 会报错，CLI 的 `--load-scale` 也已移除。当前请求时间始终为：

```text
timestamp = session 实际开始时间 + 参考请求相对时间
```

```json
{
  "block_size": 128,
  "duration": 10800,
  "max_concurrent_sessions": 32,
  "session_rate": 0.01,
  "arrival": {"distribution": "gamma", "cv": 1.5},
  "bursts": [{"start": 3600, "duration": 600, "session_rate": 0.05}],
  "seed": 42,
  "datasets": [
    {"name": "agent", "path": "traces.jsonl", "format": "weka", "weight": 1.0}
  ]
}
```

| 参数 | 控制对象与单位 |
|---|---|
| `block_size` | 每个输出 KV block 覆盖的 token 数，正整数 |
| `duration` | 合成窗口长度，秒，输出范围为 `[0, duration)` |
| `max_concurrent_sessions` | 同时活跃 session 的硬上限，正整数，必须提供 |
| `session_rate` | 新 session 的候选到达强度，session/s，非负，必须提供 |
| `arrival.cv` | Gamma 候选间隔的变异系数，默认 1；0 为均匀间隔，1 为指数间隔，>1 更突发 |
| `bursts` | 指定时间段覆盖候选到达率；每段包含 `start`、`duration`（秒）和绝对 `session_rate` |
| `seed` | 非负整数随机种子，默认 0 |
| `datasets[].weight` | 按 session 数混合的正数权重，自动归一化 |

上例通常每秒产生 0.01 个候选 session；第 60–70 分钟改为 0.05 个，之后恢复为 0.01 个。burst 窗口必须在合成窗口内且不能重叠。基础速率可以为 0，以实现仅在 burst 时段产生候选。

到达分布也支持 `{"distribution":"weibull","shape":0.7}`。它与 Gamma 一样按期望间隔校准；候选时钟通过分段速率的积分进行变换，跨 burst 边界保留尚未消耗的间隔，不重置随机相位。`arrival.cv` 只作用于 Gamma。

## session 并发的生命周期

一个 session 从第一条请求到达时开始活跃，在最后一条请求到达后立即释放。Weka subagent 的嵌套请求也计入外层 session；不能在主请求结束而 subagent 仍有后续请求时提前释放。参考 `api_time` 不参与计数。

达到并发上限后，后续候选 session 按 FIFO 等待空位。获得空位时整体平移 session 开始时间，内部所有请求间隔、顺序和前缀关系保持不变。这里等待的是尚未启动的合成 session，不是已到达请求的服务端处理队列。同一时刻先输出已有 session 请求；末次请求释放空位后，可以立即启动下一个 session。

候选到达率、实际启动率和请求 RPS 是不同统计量：

- `max_concurrent_sessions` 限制同时活跃 session 的数量。
- `session_rate` 与 `bursts` 决定候选 session 的到达速度，从而调节并发压力。
- 已经活跃的 session 按参考模板继续产生请求；burst 结束不会截断它们。
- 触及并发上限后，实际 session 启动率受空位释放影响；burst 可能形成待启动 session 积压，而不是同等比例的请求 RPS 峰值。

窗口从空载开始。结束时，已启动 session 的窗口外请求被省略，尚未启动的候选单独计数。`max_concurrent_sessions` 是上限，不承诺全程维持某个固定或平均并发数。

manifest 的 `session_concurrency` 记录并发变化事件；`offered_session_starts` 记录候选时序。每个 session 包含 `offered_start`、实际 `start`、`start_delay` 和末次请求到达时间 `end`。统计包含峰值/时间加权平均并发、实际 session 启动率、请求 RPS、延后启动数量、截止时待启动数量等。

## 参考数据与 hash

每次按数据源权重选择来源，再均匀抽取一个完整 session 模板。请求数、各轮到达间隔、上下文长度和前缀关系联合继承自模板。同一个模板重复抽到时，内部相对时序仍相同；当前未额外重采样 session 内间隔。

### Weka：`format: "weka"`

每行一个 session，读取 `id`、`block_size`、`hash_id_scope`、`requests`。模型请求读取 `t`、`in`、`hash_ids`。递归展开嵌套请求并按 `t` 稳定排序；子请求 `t` 已相对于外层 session，不能再次加上 subagent 容器的时间。

```json
{"id":"example","block_size":64,"hash_id_scope":"local","requests":[{"t":0,"in":128,"hash_ids":[10,11]},{"t":3,"in":192,"hash_ids":[10,11,12]}]}
```

`examples/weka.json` 指向本工作区的 `G35/datasets/cc-traces-weka-061326/traces.jsonl`；迁移时修改路径。该数据有 183 个 session、26,648 个主请求、18,342 个 subagent 请求，经过长会话等筛选。`in` 是源 hash 数 × 64 的输入长度代理，并非真实 tokenizer 计数。`out` 只有长度而没有内容身份，因此不会为它编造 hash；后续请求中已有的历史上下文会正常覆盖。

### 通用格式：`format: "session_jsonl"`

每行一个 session，时间字段是 `timestamp`（秒，同一 session 时钟，最早请求归零）。可以提供抽象 token 身份或源 block hash：

```json
{"id":"tokens-example","requests":[{"timestamp":0,"token_ids":[1,2,3,4,5]},{"timestamp":2,"token_ids":[1,2,3,4,5,6,7,8]}]}
{"id":"hash-example","block_size":64,"hash_id_scope":"local","requests":[{"timestamp":0,"num_tokens":130,"hash_ids":[10,11]}]}
```

TraceLab 等数据可先转为此格式；当前没有经过真实数据验证的 TraceLab 专用适配器。只有长度和时间无法恢复前缀关系，必须提供身份信息。

目标 block hash 使用 BLAKE2b-64 前缀链，逻辑为 `h_i = H(h_(i-1), 当前有序 block 数据)`。完整 block 全部覆盖，不足一个 block 的尾部丢弃；小于一个 block 的请求仍输出空 `hash_ids`。

- 有 `token_ids` 时支持任意正整数粒度。
- 只有源 block hash 时，仅支持源粒度的正整数倍。Weka 可生成 64/128/192/256 等，不能从 64-token hash 精确恢复 32 或 96 粒度。源 hash 数必须等于 `floor(num_tokens / source_block_size)`。
- 默认 `hash_id_scope: "local"`：每个抽样实例使用独立命名空间，保留内部复用，避免误将不同 session 的同名标签视为共享。
- `hash_id_scope: "global"`：保留同一数据源内的跨 session 前缀身份；不同命名数据源相互隔离。

生成标识不与特定服务框架的原生 hash ABI 对齐。

## 测试、试验与绘图

```bash
python3 -m unittest discover -s tests -v
python3 experiments/run_weka.py \
  --source /home/solidyang/workspace/G35/datasets/cc-traces-weka-061326/traces.jsonl \
  --output-dir runs/weka_v2
python3 -m venv /tmp/tracegen-plot-venv
/tmp/tracegen-plot-venv/bin/python -m pip install -r experiments/requirements-plot.txt
/tmp/tracegen-plot-venv/bin/python experiments/plot_distributions.py
```

v2 试验比较并发上限 8、上限 32、上限 32 加 burst 三个场景；不再比较旧的时间倍率。独立源数据读取器逐请求验证 block 数、公共前缀、原始 session 间隔、截止边界，并重建活跃 session 数验证上限与并发面积。`tests/golden_v2.json` 固定当前语义；`golden_v1.json` 仅保留历史基线。

本次结果与图表见 [v2 并发及 burst 验证报告](experiments/concurrency_report.md)。

绘图使用 Python / Matplotlib 实际统计，输出请求时序、逐 session 时间线，以及 `concurrency_traffic.png/.pdf`（实际并发、候选/实际 session 启动率、请求 RPS）。绘图额外依赖 NumPy/Matplotlib，不影响生成器的标准库运行方式。分析器保留读取历史 v1 产物的能力。

[旧版 Weka 报告](experiments/weka_report.md) 和 [旧版分布报告](experiments/distribution_report.md) 对应已废弃的 v1 时间缩放语义，不代表当前参数。大体积 trace 保存在忽略的 `runs/` 目录。

## ServeGen 依据

核对的 ServeGen 代码版本为 `d70c8b2d5bc45f4b60a146fbc43dc5523f871c8b`。[论文 §6.1](https://www.usenix.org/system/files/nsdi26-xiang-servegen.pdf) 描述了客户端数量、目标总到达率、随时间变化的速率和 CV；[公开生成接口](https://github.com/alibaba/ServeGen/blob/d70c8b2d5bc45f4b60a146fbc43dc5523f871c8b/servegen/construct.py) 接收 `pool, rate_fn, duration, seed`，按 client/window 采样请求；[示例](https://github.com/alibaba/ServeGen/blob/d70c8b2d5bc45f4b60a146fbc43dc5523f871c8b/examples/generate_custom.py) 使用高 CV 生成突发到达。

本仓库借鉴到达率与分布控制，将其用于 session 候选流量。活跃 session 硬上限与 FIFO 启动规则是本仓库为 KVCache trace 增加的逻辑，并非上述 ServeGen 接口已经提供的功能。
