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

每个原始数据集先由前端插件分别转换为相同格式，独立保存；合成时先按配置权重选数据集，再从该数据集中采样 session。使用真实 Weka 数据时，先转换，再生成 3 小时 trace：

```bash
python3 prepare.py weka --input /path/to/weka/traces.jsonl \
  --output runs/normalized/weka.jsonl --block-size 128
python3 generate.py --config examples/weka.json --output runs/weka_v2.jsonl \
  --duration 10800 --block-size 128 --seed 42 \
  --new-block-jitter 0.3 \
  --max-concurrent-sessions 32 --session-rate 0.01 --arrival-cv 1.5
```

配置中的相对数据路径以配置文件所在目录为基准；输出路径以当前目录为基准。`.jsonl.gz` 输出启用 gzip。每份 trace 附带 `.manifest.json`，记录输入指纹、session 来源、实际并发、流量和截止统计。

已提供 Weka、SwissAI、LMSYS 三个前端和外部插件接口。转换命令、统一 schema、数据源限制见 [前端使用说明](docs/frontends.md)。SwissAI + LMSYS 独立输入与配比配置见 [examples/mixed.json](examples/mixed.json)，调整各来源 `weight` 即可改变采样单元比例。

上面的 Weka 示例配置包含第 60–70 分钟的 burst。需要关闭时，将配置中的 `bursts` 改为 `[]`。调整 `duration` 后，也要确保所有 burst 窗口都在新的时长内。

CLI 可覆盖配置中的 `--duration`、`--block-size`、`--seed`、`--max-concurrent-sessions`、`--session-rate`、`--new-block-jitter`；`--arrival-cv` 会选择 Gamma 分布并设置其 CV。burst 时间段和数据源采样配比通过 JSON 配置调整。完整参数可用 `python3 generate.py --help` 查看。

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
  "new_block_jitter": 0.3,
  "duration": 10800,
  "max_concurrent_sessions": 32,
  "session_rate": 0.01,
  "arrival": {"distribution": "gamma", "cv": 1.5},
  "bursts": [{"start": 3600, "duration": 600, "session_rate": 0.05}],
  "seed": 42,
  "datasets": [
    {"name": "agent", "path": "runs/normalized/weka.jsonl", "format": "session_jsonl", "weight": 1.0}
  ]
}
```

| 参数 | 控制对象与单位 |
|---|---|
| `block_size` | 每个 KV block 的 token 数，必须与前端转换粒度一致；修改后需重新转换 |
| `new_block_jitter` | 新增 block 数的相对扰动幅度，范围 `[0,1]`，默认 `0.3`；`0` 恢复参考长度 |
| `duration` | 合成窗口长度，秒，输出范围为 `[0, duration)` |
| `max_concurrent_sessions` | 同时活跃 session 的硬上限，正整数，必须提供 |
| `session_rate` | 新 session 的候选到达强度，session/s，非负，必须提供 |
| `arrival.cv` | Gamma 候选间隔的变异系数，默认 1；0 为均匀间隔，1 为指数间隔，>1 更突发 |
| `bursts` | 指定时间段覆盖候选到达率；每段包含 `start`、`duration`（秒）和绝对 `session_rate` |
| `seed` | 非负整数随机种子，默认 0 |
| `datasets[].weight` | 选择数据集的正数权重，自动归一化；随后在该数据集中均匀采样 session |
| `datasets[].block_size` | 可选，声明该输入已转换的 block 粒度，用于一致性校验 |
| `datasets[].hash_id_scope` | 合成策略，默认 `local` 隔离抽样实例；`global` 保留源内跨 session 身份，需关闭该来源的扰动 |
| `datasets[].new_block_jitter` | 可选，覆盖该来源的扰动幅度；global 身份来源设为 `0` |

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

如果来源按 `request_window` 分组，这里的 session 是采样单元，不代表真实用户会话。分组和时间来源记录在转换报告中；`source_mix` 报告各数据源实际单元数、请求数和 block 引用数。

## 参考数据与 hash

每次按数据源权重选择来源，再均匀抽取一个完整 session 模板。请求数、各轮到达间隔和前缀分支关系继承自模板；默认对新增 block 数独立扰动，使同一模板的不同合成 session 也有不同的新增长度。内部相对时序仍相同；当前未额外重采样 session 内间隔。

### 新增 block 数的随机扰动

`new_block_jitter` 默认 `0.3`。对每个有新增前缀的请求，采样一个倍率 `f ~ Uniform(1-jitter, 1+jitter)`，对本次首次出现的前缀段长度乘以 `f`，再做随机取整。同一请求的各个新增段共享这个倍率，已经生成的段不再改变。不同 session 使用由 `seed` 和 `session_id` 派生的独立随机流；调整该参数不会改变抽到的模板、请求时间或 session 并发。

例如参考新增长度为 100 block、没有中间分叉时，`0.3` 会在约 70–130 block 内变化，`0.6` 会在约 40–160 block 内变化。这里的新增是相对于该 session 的全部历史请求，不是与上一请求总长度作差；首请求也参与扰动，历史完全重复的请求保持零新增。

为保留复用结构，先从完整模板识别请求结束点和分叉点，再缩放这些点之间的前缀段，每段至少保留 1 block。这样未来分支、较短历史请求以及非相邻分支的重访仍有一致的共享前缀。随机取整和最少 1 block 的约束会使短段偏离理想倍率，尤其单 block 段不能缩为零；参数是扰动幅度，不是最终新增量的精确 CV，也不保证有限样本总 block 数不变。

输出覆盖的是扰动后合成上下文的全部完整 block，新增长度的 block 口径可乘 `block_size` 换算。没有生成真实文本或额外伪造输出 token；源数据不足一 block 的尾部不会被当成完整 block。

设置 `--new-block-jitter 0` 可恢复参考 block 数；本次最小格式迁移会改变 hash 编码，不承诺旧版 hash 字节兼容。扰动仅支持数据集配置 `hash_id_scope: "local"`；全局身份数据需要显式设为 `0`，可在对应的 `datasets[]` 项中覆盖，保留其他来源的扰动。

manifest 的 `block_variation` 记录算法和幅度；启用时，整体及各 session 统计包含 `reference_new_blocks`、`synthetic_new_blocks` 和 `requests_with_changed_new_blocks`，范围仅含实际输出的请求，按各采样单元内部历史计数。global 数据的该计数不等于跨单元全局去重后的块数。

### 原始数据前端

| 插件 | 处理内容 |
|---|---|
| `weka` | 展开嵌套请求，转换 `t/in`，保留观测相对时序和源身份 |
| `swissai` | 按精确 token 数去除 padding 尾 bucket，排序并按明确的字段或时间窗口分组 |
| `lmsys` | 按 conversation 展开请求，用目标 tokenizer/chat template 编码完整历史；缺失的逐轮时间使用显式 Gamma 模型 |

后端不再接受 `format: "weka"` 或其他原始格式；使用 `prepare.py` 转换。`examples/weka.json` 现在指向 `runs/normalized/weka.jsonl`。Weka 的 `in` 仍是源 hash 数 × 64 的长度代理；LMSYS 的 token 长度来自所选 tokenizer，但时间为合成值。详细口径见 [前端文档](docs/frontends.md)。

### 唯一后端输入：`format: "session_jsonl"`

每行一个 session，只有 `requests`；每条请求只有相对到达时间 `timestamp`（秒）和完整 block 的 `hash_ids`：

```json
{"requests":[{"timestamp":0,"hash_ids":[10,11]},{"timestamp":2,"hash_ids":[10,11,12]}]}
```

没有 session ID、token 数、token 序列或其他元数据字段。转换后的每份数据集独立保存，合成配置用 `datasets[].path` 和 `weight` 分别指定。session 来源通过文件和行号定位，合成时分配新的 `session_id`。

前端按 `--block-size` 划分完整 block，并生成体现完整前缀的 hash。尾部不满一 block 时丢弃；前端结果中的空 `hash_ids` 请求在后端采样前剔除，全为空的 session 排除；合成输出不允许空 `hash_ids`。有效请求之间的时间间隔保持不变，首个有效请求归零。后端不再根据 token 数补齐或重新划分 block，只处理这些身份的复用和新增 block 扰动。

转换报告单独存为 `.manifest.json`，后端校验其中的输出指纹和 block size。手工提供最小 JSONL 时无需报告，合成配置声明其粒度。转换后改用其他 block size，必须重新运行前端。旧的 token_ids/num_tokens 等富字段输入也需要重新转换。

同名标签在不同前缀位置会由后端形成不同身份。默认每个抽样实例独立，保留内部复用；需要保留某来源跨 session 身份时，在该数据集的合成配置中设置 `hash_id_scope: "global"` 和 `new_block_jitter: 0`。不同命名数据源始终隔离。

生成标识不与特定服务框架的原生 hash ABI 对齐。

## 按最终请求数配比合成

`datasets[].weight` 仍是 session 抽样权重。若要控制**最终有效请求数比例**，用校准实验脚本，不能直接把请求比例填成 session 权重：

```bash
python3 prepare.py weka --input /path/to/weka/traces.jsonl \
  --output runs/normalized/weka.jsonl --block-size 128
# SwissAI 和 LMSYS 分别转换，命令见 docs/frontends.md。
/tmp/tracegen-plot-venv/bin/python experiments/run_mixed.py \
  --weka runs/normalized/weka.jsonl \
  --swissai runs/normalized/swissai.jsonl \
  --lmsys runs/normalized/lmsys.jsonl --lmsys-name lmsys \
  --ratios 0.4 0.4 0.2 --duration 10800 \
  --session-rate 0.2 --max-concurrent-sessions 32 \
  --tolerance 0.01 --output-dir runs/mixed_requests
```

比例顺序为 Weka / SwissAI / LMSYS。`--tolerance 0.01` 表示每个来源的实际请求占比与目标相差不超过 **1 个百分点**。不传 `--ratios` 时对比 `40/40/20`、`70/20/10`、`10/20/70` 三组。默认 LMSYS 路径指向人工样例，真实数据应显式传入并设置来源名。

校准先按“目标请求比例 / 每个有效 session 的平均请求数”初始化 session 权重，再仅回放请求计数来校正有限窗口、并发准入和末尾截断影响。达到误差要求后才生成 hash trace，逐条核对实际输出与计数预测。不会为了配比额外删除有效请求；整数 session 抽样无法保证任意比例绝对精确，未达容差时明确报错。

输出包括单个按到达时间排序的合成 JSONL、已校准 `.config.json`、校准记录、配比/时序/前缀复用报告和图。校准配置可直接传给 `generate.py` 重现；改动输入、时长、随机种子、并发或流量参数后需重新校准。各来源的空请求和全空 session 剔除计数记录在 manifest 的 `sources[].filtering` 中。

## 测试、试验与绘图

```bash
python3 -m unittest discover -s tests -v
python3 experiments/run_weka.py \
  --source /home/solidyang/workspace/G35/datasets/cc-traces-weka-061326/traces.jsonl \
  --output-dir runs/weka_v2
python3 experiments/run_block_variation.py \
  --source /home/solidyang/workspace/G35/datasets/cc-traces-weka-061326/traces.jsonl \
  --output-dir runs/weka_block_variation
python3 -m venv /tmp/tracegen-plot-venv
/tmp/tracegen-plot-venv/bin/python -m pip install -r experiments/requirements-plot.txt
/tmp/tracegen-plot-venv/bin/python experiments/plot_distributions.py
```

v2 试验比较并发上限 8、上限 32、上限 32 加 burst 三个场景；不再比较旧的时间倍率。独立源数据读取器逐请求验证 block 数、公共前缀、原始 session 间隔、截止边界，并重建活跃 session 数验证上限与并发面积。`tests/golden_nonempty_v1.json` 固定最小输入格式下关闭扰动的基线；`tests/golden_nonempty_variation_v1.json` 固定新增 block 扰动结果。旧 golden 文件保留作为迁移前历史基线。

`run_weka.py` 显式关闭长度扰动，作为精确复用参考模板的基线；`run_block_variation.py` 比较同一时间线上的 `0 / 0.3 / 0.6` 扰动，独立验证请求端点和分叉拓扑、历史复用及跨 session 隔离，并统计新增 block 分布。

并发基线结果与图表见 [v2 并发及 burst 验证报告](experiments/concurrency_report.md)。

统计现有 trace 的请求时序与 block 前缀复用，可运行：

```bash
/tmp/tracegen-plot-venv/bin/python experiments/analyze_prefix_reuse.py \
  --traces runs/minimal_trial/weka_real_variation/weka_jitter_0.3.jsonl \
  --output-dir runs/prefix_analysis
```

省略 `--traces` 时分析最新最小格式试验的五份 trace。输出图、逐请求/60 秒分桶 CSV 及 JSON/Markdown 报告。复用按到达顺序查询此前请求的最长连续完整 block 前缀，分别统计 session 内、跨 session 额外贡献，并区分重复抽到同一源模板与不同源模板的共享；不模拟缓存淘汰。分析器仍兼容含空 hash 请求的历史 trace；新合成结果不含这类请求。

绘图使用 Python / Matplotlib 实际统计，输出请求时序、逐 session 时间线，以及 `concurrency_traffic.png/.pdf`（实际并发、候选/实际 session 启动率、请求 RPS）。绘图额外依赖 NumPy/Matplotlib，不影响生成器的标准库运行方式。分析器保留读取历史 v1 产物的能力。

[旧版 Weka 报告](experiments/weka_report.md) 和 [旧版分布报告](experiments/distribution_report.md) 对应已废弃的 v1 时间缩放语义，不代表当前参数。大体积 trace 保存在忽略的 `runs/` 目录。

## ServeGen 依据

核对的 ServeGen 代码版本为 `d70c8b2d5bc45f4b60a146fbc43dc5523f871c8b`。[论文 §6.1](https://www.usenix.org/system/files/nsdi26-xiang-servegen.pdf) 描述了客户端数量、目标总到达率、随时间变化的速率和 CV；[公开生成接口](https://github.com/alibaba/ServeGen/blob/d70c8b2d5bc45f4b60a146fbc43dc5523f871c8b/servegen/construct.py) 接收 `pool, rate_fn, duration, seed`，按 client/window 采样请求；[示例](https://github.com/alibaba/ServeGen/blob/d70c8b2d5bc45f4b60a146fbc43dc5523f871c8b/examples/generate_custom.py) 使用高 CV 生成突发到达。

本仓库借鉴到达率与分布控制，将其用于 session 候选流量。活跃 session 硬上限与 FIFO 启动规则是本仓库为 KVCache trace 增加的逻辑，并非上述 ServeGen 接口已经提供的功能。
