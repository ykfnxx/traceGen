# traceGen

[English](README.md) | [简体中文](README_ZH.md)

traceGen 用于合成 KVCache 管理实验所需的请求到达 trace。各原始数据集先分别转换为最小 session JSONL 文件，再通过 JSON 配置指定输入文件和每个来源的流量参数，独立生成各来源的请求流，最后按时间合并。

**数据集名称和数量不固定。配置中列出哪些文件，就读取哪些文件。** Weka、SwissAI、LMSYS 是可选的原始数据转换前端，不是合成器限定的来源类型。

只模拟请求到达，不模拟推理执行、请求完成、服务容量或真实文本生成。保留参考 session 内的相对时序和前缀结构，支持对新增 block 长度进行扰动。

## 1. 安装与快速运行

需要 Python 3.10+。核心生成器只依赖标准库；混合实验入口还会分析和绘图，需要 NumPy 和 Matplotlib。

```bash
git clone git@github.com:ykfnxx/traceGen.git
cd traceGen
python3 -m venv .venv
.venv/bin/python -m pip install -r experiments/requirements-plot.txt
.venv/bin/python experiments/run_mixed.py \
  --config examples/config.example.json \
  --output-dir runs/example
```

[config.example.json](examples/config.example.json) 使用仓库内置的人工样例，不需要下载外部数据。示例时长 600 秒、block 大小为 4 tokens，目标请求比例为 60%/40%。这些小样例用于验证流程，不能代表真实生产负载分布。

主要输出是 `runs/example/mixed.jsonl`。使用保存后的配置复现，不重新校准或绘图：

```bash
.venv/bin/python generate.py \
  --config runs/example/mixed.config.json \
  --output runs/example/replay.jsonl
```

如果只想用标准库验证生成器，可运行旧公共时钟模式的内置示例：

```bash
python3 generate.py --config examples/demo.json --output runs/demo.jsonl
```

## 2. 准备自己的数据

```text
原始数据集 A → 前端 A → a.sessions.jsonl ┐
原始数据集 B → 前端 B → b.sessions.jsonl ├→ 配置各来源独立流量 → 合并 trace
原始数据集 C → 前端 C → c.sessions.jsonl ┘
```

### 最小统一输入格式

JSONL 每行一个 session，只有 `requests` 字段；每条请求只有 `timestamp` 和 `hash_ids`：

```json
{"requests":[{"timestamp":0,"hash_ids":[101,102]},{"timestamp":2.5,"hash_ids":[101,102,103]}]}
{"requests":[{"timestamp":0,"hash_ids":[201]}]}
```

- `timestamp`：有限、非负的 session 内相对时间，单位秒。前端按时间组织请求并将首条归零，允许相同时间。
- `hash_ids`：完整 KV block 的有序身份，可为整数或字符串，需要体现参考数据的前缀共享关系。转换时排除不足一个 block 的尾部。
- 空 `hash_ids` 请求在采样前剔除；没有有效请求的 session 排除。有效请求间隔不变，首个有效请求重新归零。整个来源都没有有效 session 时明确报错。
- 不需要输入 session ID、token 数、provenance、namespace 等字段，多余字段会被拒绝。输出 session ID 由合成器生成。

所有输入必须使用相同 block 大小。合成器不做 tokenizer 编码或 block 重新分组；修改 block 大小必须重新转换。`prepare.py` 另存 `.manifest.json`，记录转换参数、指纹和 block 大小，后端发现该文件时会校验。手工准备的最小 JSONL 不强制要求这个附属文件。

### 内置前端

在项目根目录执行，替换占位路径：

```bash
python3 prepare.py weka --input /path/to/weka/traces.jsonl \
  --output runs/normalized/weka.jsonl --block-size 128

python3 prepare.py swissai --input /path/to/qwen3-32b-buckets.jsonl \
  --output runs/normalized/swissai.jsonl --block-size 128 \
  --session-mode window --window-seconds 300 --bucket-size 16

.venv/bin/python -m pip install -r requirements-frontends.txt
.venv/bin/python prepare.py lmsys --input /path/to/lmsys/*.parquet \
  --output runs/normalized/lmsys.jsonl --block-size 128 \
  --tokenizer /path/to/tokenizer \
  --turn-interval-mean 30 --turn-interval-cv 1 --seed 42
```

| 前端 | 处理方式与边界 |
|---|---|
| `weka` | 展开嵌套请求，保留参考相对时序。目标 block 大小必须是源 64-token block 的正整数倍。源 `in` 是长度代理，并非精确 tokenizer 计数。 |
| `swissai` | 需要 bucket 身份、精确 token 数和时间戳；剔除 padding 尾部，按明确的会话字段或时间窗口分组。窗口是采样单元，不是观测到的用户 session。 |
| `lmsys` | 使用指定 chat template 编码对话历史，每个已记录 assistant 回复对应一次输入请求。原数据缺少逐轮到达时间，因此另行合成时间。受访问限制的原始文件需自行获取。 |

原始 schema 和插件接口见[前端详细说明](docs/frontends.md)。新前端实现 `configure(parser)` 和 `convert(records, options, context)`，通过 `prepare.py module:Class` 加载。只要输出上述统一格式，就不需要修改合成器。

### 转换参数

| 参数 | 默认值 / 含义 |
|---|---|
| `frontend` | 必填位置参数：`weka`、`swissai`、`lmsys` 或 `module:Class`。 |
| `--input` | 必填，可指定多个原始 JSONL、JSONL.gz 或 Parquet 文件；多个文件表示一个来源的分片。Parquet 需要 PyArrow。 |
| `--output` | 必填，输出未压缩的统一 session JSONL。 |
| `--limit` | 默认读取全部；正整数表示最多读取的原始记录数，不是输出 session 数。 |
| `--block-size` | `128`，统一 block 的 token 数，必须与合成配置一致。 |
| SwissAI `--session-mode` | 必填：`window` 或 `field`。 |
| SwissAI `--window-seconds` | `300`，窗口分组的时间宽度，秒。 |
| SwissAI `--session-field` | `session_id`，字段分组时使用的真实输入字段。 |
| SwissAI `--bucket-size` | `16`，源 bucket 的 token 数，必须与原始数据版本一致。 |
| SwissAI `--token-count-field` | `token_count`，精确长度字段名。 |
| SwissAI `--model` | 可选模型筛选条件，也可为缺少模型字段的输入提供模型名。 |
| LMSYS `--tokenizer` | 必填，tokenizer 仓库名或本地目录。 |
| LMSYS `--revision` | 可选，固定 tokenizer 版本。 |
| LMSYS `--turn-interval-mean` | `30`，合成逐轮间隔均值，秒。 |
| LMSYS `--turn-interval-cv` | `1`，逐轮 Gamma 间隔的 CV；`0` 为固定间隔。 |
| LMSYS `--seed` | `42`，逐轮时间合成种子，与后端合成种子独立。 |
| LMSYS `--model`、`--language` | 可选的原始标签筛选；模型筛选不决定目标 tokenizer。 |

可运行 `python3 prepare.py <frontend> --help` 查看对应前端的参数。

## 3. 配置独立来源

以 [examples/config.example.json](examples/config.example.json) 为起点。下面的完整示例应保存于 `examples/`，以便相对路径正确指向内置数据：

```json
{
  "block_size": 4,
  "duration": 600,
  "seed": 42,
  "new_block_jitter": 0.3,
  "datasets": [
    {
      "name": "chat",
      "path": "data/chat.jsonl",
      "request_ratio": 0.6,
      "traffic": {"session_rate": 0.2, "arrival": {"cv": 0.7}}
    },
    {
      "name": "agent",
      "path": "data/agent.sessions.jsonl",
      "request_ratio": 0.4,
      "traffic": {
        "session_rate": 0.1,
        "arrival": {"cv": 2},
        "bursts": [{"start": 200, "duration": 100, "session_rate": 0.4}]
      }
    }
  ],
  "calibration": {"tolerance": 0.02, "max_iterations": 150}
}
```

增删 `datasets` 数组项即可增删来源。`name` 是自定义标签，不是前端类型。`path` 直接指定统一格式文件，支持绝对路径或相对于配置文件目录的路径。输出路径以运行命令的当前目录为基准。移动配置文件后要相应调整输入路径。

换成真实数据时，替换文件路径并将 `block_size` 改为转换使用的大小。`duration: 10800` 是 3 小时，`86400` 是一天。所有 burst 窗口都必须位于该时长范围内。

### 顶层参数

| 字段 | 默认值 | 含义与约束 |
|---|---|---|
| `block_size` | 必填 | 正整数，每个 block 的 token 数，所有输入必须一致。 |
| `duration` | 必填 | 正数，秒；输出窗口为 `[0, duration)`。 |
| `datasets` | 必填 | 非空数据源数组，名称和数量不固定。 |
| `seed` | `0` | 非负整数，合成随机种子。 |
| `new_block_jitter` | `0.3` | `[0,1]` 内的新增 block 扰动幅度；`0` 保留参考长度。 |
| `max_concurrent_sessions` | 不限 | 独立流模式中省略或设 `null` 表示不限；正整数表示全局活跃 session 上限，`0` 无效。 |
| `calibration.tolerance` | `0.01` | 各来源请求占比的绝对误差容限，严格介于 0 和 1；`0.01` 是 1 个百分点。有目标比例时使用。 |
| `calibration.max_iterations` | `150` | `run_mixed.py` 最大校准次数，正整数。 |

### 来源参数：`datasets[]`

| 字段 | 默认值 | 含义与约束 |
|---|---|---|
| `name` | 必填 | 唯一非空字符串，用于报告、session ID 和随机种子。 |
| `path` | 必填 | 统一 session JSONL 文件路径。 |
| `traffic` | 必填 | 独立的候选 session 到达配置，见下表。 |
| `request_ratio` | 不设置 | 最终输出请求占比的正数相对权重，自动归一化；必须所有来源都填或全部省略。 |
| `rate_scale` | `1` | 正数，乘在该来源的基础及 burst 速率上；校准会将有效倍率写入该字段。 |
| `new_block_jitter` | 继承顶层 | 覆盖该来源的 block 扰动幅度。 |
| `hash_id_scope` | `local` | `local` 隔离抽样实例并保留 session 内共享；`global` 保留来源内跨实例身份，要求有效 jitter 为 `0`。不同命名来源仍隔离。 |
| `block_size` | 继承顶层 | 可选的输入粒度声明，必须与全局值相同。 |
| `format` | `session_jsonl` | 唯一支持的统一格式，原始数据需先转换。 |

独立流模式拒绝 `datasets[].weight`，以及顶层 `session_rate`、`arrival`、`bursts`。到达参数写在每个来源自己的 `traffic` 中。

### 到达参数：`datasets[].traffic`

| 字段 | 默认值 | 含义与约束 |
|---|---|---|
| `session_rate` | 必填 | 非负候选 session/s，尚未乘 `rate_scale`。设为 0 可关闭基础流量，并单独配置 burst。 |
| `arrival.distribution` | `gamma` | `gamma` 或 `weibull`。 |
| `arrival.cv` | `1` | Gamma 到达间隔的 CV，非负。`0` 为均匀间隔，`1` 为指数间隔，更大则间隔变化更强；仅对 Gamma 生效。 |
| `arrival.shape` | `1` | 正数，Weibull 形状参数，仅对 Weibull 生效；均值按配置速率归一化。 |
| `bursts` | `[]` | 该来源的速率覆盖窗口，窗口之间不能重叠。 |
| `bursts[].start` | 必填 | 非负起始时间，秒。 |
| `bursts[].duration` | 必填 | 正数窗口长度，秒；整个窗口必须在合成时长内。 |
| `bursts[].session_rate` | 必填 | 窗口内的非负绝对候选速率，替换基础速率后再乘 `rate_scale`。 |

Weibull 示例：`"arrival": {"distribution": "weibull", "shape": 0.7}`。不同来源可以使用不同分布。跨速率边界保留积分时间中尚未消耗的到达间隔。允许同时刻到达，包含间隔小于 float64 时间分辨率的情况。

## 4. 理解速率、比例、并发和 hash

### session 到达率不等于请求 RPS

基础速率固定时，候选量的近似尺度为：

```text
候选 session 数 ≈ duration × session_rate × rate_scale
```

存在 burst 时应积分计算分段有效速率。随机抽样会使实际数量变化。每个准入的 session 根据模板产生多条请求，并发准入和末尾截止会影响实际输出量。因此 `duration=86400, session_rate=0.2` 在倍率为 1 时对应约 17,280 个候选 session，不是 17,280 个请求。

`arrival.cv` 是 session 到达间隔的 CV，不是每秒请求数的 CV，也不是目标活跃 session 数。

### 最终请求占比校准

不填 `request_ratio` 时直接使用各来源配置的速率。填写后，`run_mixed.py` 调整各来源的 `rate_scale`，让 FIFO 准入和截止后的非空请求占比达到目标。校准保持各来源有效候选速率在全窗口内的积分之和，不保持每个来源原有速率，也不保证有限样本的最终请求总数固定。CV 和 burst 时间段不变。

日志 `Generating mixed: expected counts ...` 是计数回放预测的各来源最终请求数，不是 session 数；实际生成后会核对。不会为了满足比例额外删除有效请求。窗口过短、session 过长或并发上限过紧，都可能导致校准无法达到容差；失败时明确报错。零流量来源无法满足正数目标比例。

保存的 `mixed.config.json` 包含有效倍率，并移除了待校准比例。用 `generate.py` 在相同输入和实现下复现。修改输入、seed、时长、流量或并发上限后，应重新校准。

### 可选的活跃 session 上限

session 从首次请求到达开始活跃，在最后一条请求到达后立即释放名额。设置上限时，候选 session 按 FIFO 等待空位，整体推迟开始时间：

```text
请求 timestamp = session 实际准入时间 + 参考请求相对时间
```

来源和模板在准入前已经确定。同刻先处理已有请求，候选按来源名称和源内顺序排列。session 内请求间隔不变。不设上限时直接合并独立流。该参数是上限，不是目标平均并发；释放名额不等待请求执行完成。

从空载开始生成；时间达到或超过 `duration` 的请求省略。已准入但被截断的 session，与截止时尚在等待的候选分别计数。SwissAI 时间窗口属于采样单元，其并发不等于真实用户会话并发。

### 新增 block 扰动与前缀复用

设置 `new_block_jitter=j` 后，对一次请求首次遇到的前缀段采样共同倍率 `Uniform(1-j, 1+j)`，缩放段长度并随机取整，每段至少保留一个 block。已经生成的段、请求结束点和分叉关系保持一致。这里的“新增”相对于该采样 session 的全部历史，不是仅与上一条请求比较；完全重复的请求没有新增 block。

扰动只改变 block 长度，不改变到达时间或模板选择。短段受随机取整和至少一块的限制，可能偏离理想倍率，也不保证总 block 数固定。需要来源内跨 session 共享时，设置 `hash_id_scope: "global"` 和 `new_block_jitter: 0`；重复抽到模板会重访同一源内容。

全局 seed 和来源名称派生独立随机流。在倍率固定且没有并发准入延迟时，增删、重排或调整其他来源，不会改变当前来源的 trace。校准会有意联动有效速率。重命名来源会改变随机序列和身份。输出 hash 不保证与某个推理框架的原生 hash ABI 一致。

## 5. 命令和输出结果

### 命令行入口

| 命令 | 参数 |
|---|---|
| `experiments/run_mixed.py` | 必填 `--config`；可选 `--output-dir`，默认项目目录下 `runs/mixed`。数据源、时序和比例都写入 JSON；生成后自动验证、分析和绘图。 |
| `generate.py` | 必填 `--config`、`--output`。输出 JSONL，输出后缀为 `.gz` 时压缩，并写 manifest。不校准、不分析；输入配置不能包含 `request_ratio`。 |
| `experiments/analyze_prefix_reuse.py` | `--traces` 后接一个或多个已生成 trace 路径；可选 `--labels`、`--output-dir`，默认 `runs/minimal_trial/distribution_analysis`。需要对应 manifest；建议显式传路径，避免读取历史默认输入。 |

`generate.py` 支持以下可选顶层覆盖：

| 参数 | 作用 |
|---|---|
| `--block-size`、`--duration`、`--seed` | 覆盖对应 JSON 字段，仍须满足输入粒度和 burst 边界校验。 |
| `--max-concurrent-sessions` | 设置正整数全局上限；关闭时需在独立流 JSON 中删除字段或设 `null`。 |
| `--new-block-jitter` | 覆盖顶层默认值，各来源显式设置仍优先。 |
| `--session-rate`、`--arrival-cv` | 仅适用于旧公共时钟配置；后者选择 Gamma。独立流配置拒绝这些全局到达字段。 |

### 输出文件与指标

| 文件 | 内容 |
|---|---|
| `mixed.jsonl` | 按到达时间排序的请求。 |
| `mixed.jsonl.manifest.json` | 有效配置、源文件指纹及剔除计数、session 来源、并发时间线和生成统计。 |
| `mixed.config.json` | 可复现配置，源文件路径已解析为绝对路径。 |
| `report.json` | 启用时的校准历史、实际来源配比、时序与 block 前缀分析。 |
| `00_mixed_requests.csv`、`00_mixed_60s.csv` | 逐请求和 60 秒聚合统计；空 trace 不输出逐请求 CSV。 |
| `mixed_arrivals_prefix_reuse.png`、`.pdf` | Python/Matplotlib 根据实际统计绘制的请求到达及前缀复用图。 |

输出记录示例：

```json
{"timestamp":5.85,"hash_ids":[123,456],"session_id":"chat:00000000"}
```

| 指标 | 含义 |
|---|---|
| manifest `stats.requests`、`stats.blocks` | 输出请求数、block 引用总数；引用包含重复访问。 |
| `offered_sessions`、`sessions`、`pending_sessions_at_end` | 候选数、实际准入数、截止时仍在等待的候选数。 |
| `truncated_sessions`、`omitted_requests_at_end` | 有尾部请求被省略的已准入 session 数、这些省略请求的数量。 |
| `actual_rps`、`actual_session_rate` | 输出请求数 / 时长；准入 session 数 / 时长。 |
| `peak_concurrent_sessions`、`mean_concurrent_sessions` | 峰值活跃数、按时间加权的平均活跃数。 |
| `delayed_sessions`、`mean_start_delay_seconds`、`max_start_delay_seconds` | 准入延迟统计；平均值对所有已准入 session 计算，包含零延迟。 |
| manifest `source_mix` | 独立模式下各来源候选数、有效倍率、准入 session 数、请求数和 block 引用数。 |
| report `runs[].analysis.unique_hashes` | 输出 trace 中全局不同 block 的数量，不是 manifest 的 `unique_templates`。 |
| `cross_session_unique_hashes` | 至少出现在两个输出 session 中的不同 block 数。 |
| `reused_rate`、`within_rate`、`cross_extra_rate` | 按 block 引用加权的历史前缀复用率：总复用、session 内复用、跨 session 额外贡献。 |

单独运行 `generate.py` 不统计全局 unique block；可以读取混合实验报告：

```bash
python3 - <<'PY'
import json
with open('runs/example/report.json') as f:
    report = json.load(f)
for run in report['runs']:
    a = run['analysis']
    print(run['name'], 'requests:', a['requests'],
          'unique blocks:', a['unique_hashes'], 'prefix reuse:', a['reused_rate'])
PY
```

复用统计只表示历史复用机会，没有缓存容量、淘汰或 TTL 模型，不能直接理解为实际缓存命中率。长上下文不断追加的负载可能贡献大量重复 block，即使请求数配比均衡，按 block 加权的复用率仍可能很高。

## 6. 验证与常见问题

```bash
python3 -m unittest discover -s tests -v
.venv/bin/python experiments/analyze_prefix_reuse.py \
  --traces runs/example/mixed.jsonl --output-dir runs/example_analysis
```

可选前端测试依赖 `requirements-frontends.txt`。测试覆盖过滤、前缀一致性、扰动、独立流、FIFO 准入、截止、校准预测与生成一致性，以及任意来源文件列表。大体积输出保存在被 Git 忽略的 `runs/` 下。

| 问题 | 处理方式 |
|---|---|
| 找不到文件 | 输入相对路径以配置文件目录为基准，不是 shell 当前目录；替换原始数据的占位路径。 |
| block 大小不匹配 | 按统一粒度重新转换输入，仅修改合成配置不会重新分组 block。 |
| 过滤后来源为空 | 确保至少有一个 session 包含拥有完整 block 的有效请求。 |
| 校准失败 | 检查 session 长度和截止影响，可增大时长/流量、放宽容差或调整并发上限；这些操作会改变负载。 |
| global 身份与非零 jitter 冲突 | 将该来源 `new_block_jitter` 设为 `0`，或改为 local。 |
| 旧版本报 `session offer clock lost precision` | 更新实现；当前代码允许并保留浮点舍入后的同刻到达。 |

`generate.py` 仍接受旧公共时钟 JSON：在顶层配置 `session_rate`、`arrival`、`bursts`，必须提供正整数 `max_concurrent_sessions`，并使用正数 `datasets[].weight`（默认 `1`）先选来源，再在该来源中均匀采样 session。示例见 [demo.json](examples/demo.json)、[weka.json](examples/weka.json)。不要与来源内 `traffic` 混用。

旧的 `run_mixed.py --weka/--swissai/--lmsys/--ratios` 入口已移除，统一使用配置中的文件列表。`load_scale`、`base_session_rate` 也已移除。目前尚未实现时间窗口参数自动拟合和 NB 微突发生成。
