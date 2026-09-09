# traceGen

按照参考数据中的完整 session 模板合成 LLM serving trace，输出按时间排序的请求：

```json
{"timestamp":5.8519693975079745,"hash_ids":[123,456],"session_id":"s00000000"}
```

`timestamp` 是从合成开始计时的秒数；`hash_ids` 是有序的 uint64 KV block 前缀标识；每个完整 block 都被覆盖，不足一个 block 的尾部丢弃。小于一个 block 的请求仍输出一行，其 `hash_ids` 为空。

## 快速运行

只依赖 Python 标准库，要求 Python 3.10+；当前测试环境为 Python 3.14.4。

```bash
cd /home/solidyang/workspace/traceGen
python3 generate.py --config examples/demo.json --output runs/demo.jsonl
python3 generate.py --config examples/weka.json --output runs/weka.jsonl --load-scale 2
python3 -m unittest discover -s tests -v
```

`examples/data/` 是小型人工示例，用于演示和测试；`examples/weka.json` 指向本工作区已有的真实 Weka 数据。迁移到其他机器时修改数据路径。配置中的相对数据路径以配置文件目录为基准，输出路径以当前目录为基准。输出后缀为 `.gz` 时启用 gzip。

每次生成附带 `<output>.manifest.json`，记录配置、源文件 SHA256、实际请求强度、截断数量，以及每个合成 session 对应的源 session。相同输入、种子和 Python 随机数实现下，未压缩 JSONL 内容可复现。

## 配置与时序

```json
{
  "block_size": 128,
  "duration": 3600,
  "load_scale": 2.0,
  "base_session_rate": 0.01,
  "seed": 42,
  "arrival": {"distribution": "gamma", "cv": 1.5},
  "datasets": [
    {"name": "agent", "path": "traces.jsonl", "format": "weka", "weight": 1.0}
  ]
}
```

| 参数 | 含义 |
|---|---|
| `block_size` | 输出 KV block 的 token 粒度，正整数 |
| `duration` | 输出时间窗口长度，秒，严格保留 `[0, duration)` 内的请求 |
| `load_scale` | 整体时序强度倍率，正数，默认 1 |
| `base_session_rate` | 基准 session 到达率，session/s，正数，必须显式提供 |
| `seed` | 非负整数随机种子，默认 0 |
| `arrival` | session 起始间隔分布，默认 Gamma、CV=1 |
| `datasets[].weight` | 正数权重，按 session 数进行混合，自动归一化 |

每个 session 先按数据源权重选来源，再均匀抽取一条完整 session 模板。session 的请求数、各轮到达间隔、上下文长度和前缀关系共同继承自模板。因此不同 session 的差异来自参考样本，同一个模板被重复抽到时也会保留它原本的内部时序。第一版尚未加入同类 session 内的间隔重采样。

session 起始间隔使用以下分布之一：

- Gamma：`{"distribution":"gamma","cv":1.5}`，形状为 `1/cv²`，尺度为 `cv²/base_session_rate`；CV=1 对应指数间隔。
- Weibull：`{"distribution":"weibull","shape":0.7}`，尺度按均值 `1/base_session_rate` 校准。

这借鉴了 [ServeGen 的 Gamma/Weibull 间隔采样思路](https://github.com/alibaba/ServeGen/blob/main/servegen/construct.py)，实现使用标准库，不依赖 ServeGen 包。当前 session 内时间来自模板经验分布；`arrival` 参数是显式配置，不声称已经从 Weka 自动拟合出跨 session 的生产到达分布。

设基准 session 起始时间为 `S`，模板内相对时间为 `r`：

```text
timestamp = (S + r) / load_scale
```

生成器在基准时间 `[0, duration * load_scale)` 内采样 session，统一压缩或拉伸所有请求时间。`load_scale=2` 同时压缩 session 间与 session 内间隔。固定种子下，较低强度的请求序列是较高强度序列的基准时间前缀，公共请求的 session ID 和 hash 保持相同。

`base_session_rate` 有必要独立配置：只有 session 内相对时间的数据无法提供真实跨 session 到达率。它不是请求 RPS，也不是并发数。不同数据源的请求 RPS 占比还取决于 session 请求数和持续时间。

时间零点没有预先活跃的 session。截止时间之后的后续请求会被省略，manifest 中记录 `truncated_sessions` 和 `omitted_requests_at_end`。由于起始阶段和截止边界影响，固定有限窗口内的实际 RPS 不保证严格按倍率增长。本脚本生成离线到达序列，不根据服务完成时间或系统负载调整后续请求。

## 输入数据

### Weka：`format: "weka"`

每行是一个 session，读取 `id`、`block_size`、`hash_id_scope` 和 `requests`。模型请求读取 `t`、`in` 和 `hash_ids`。嵌套 `requests` 递归展开，全部沿用外层 session ID，然后按 `t` 稳定排序。Weka 子请求的 `t` 已相对于外层 session，不能重复加上 subagent 容器的 `t`。

```json
{"id":"example","block_size":64,"hash_id_scope":"local","requests":[{"t":0,"in":128,"hash_ids":[10,11]},{"t":3,"in":192,"hash_ids":[10,11,12]}]}
```

本工作区的 `cc-traces-weka-061326` 源文件包含 183 个 session、26,648 个主请求和 18,342 个 subagent 请求。其 `in` 是源 hash 数 × 64 的上下文长度代理，并非真实 tokenizer 计数。数据经过长会话等筛选，不能代表所有生产请求类型。

### 通用格式：`format: "session_jsonl"`

每行一个 session。时间字段为 `timestamp`，单位秒，处于同一个 session 时钟；最早请求归零。可以提供 token 标识或完整源 block hash：

```json
{"id":"tokens-example","requests":[{"timestamp":0,"token_ids":[1,2,3,4,5]},{"timestamp":2,"token_ids":[1,2,3,4,5,6,7,8]}]}
{"id":"hash-example","block_size":64,"hash_id_scope":"local","requests":[{"timestamp":0,"num_tokens":130,"hash_ids":[10,11]}]}
```

`token_ids` 可以是抽象 token 身份，不要求文本；相同前缀必须使用相同的身份序列。hash 输入中，`len(hash_ids)` 必须严格等于 `floor(num_tokens / source_block_size)`。不补随机块，也不静默忽略缺失的完整块。

其他数据集（例如 TraceLab）可以先转成此格式再混合使用。当前没有未经实际数据验证的 TraceLab 专用字段适配器；仅有长度和时间的数据无法恢复前缀关系，需要提供相应的身份信息。

## hash 与粒度

目标 block 标识使用 BLAKE2b-64 前缀链，逻辑为 `h_i = H(h_(i-1), 当前有序 block 数据)`。此前任意位置改变，后续标识都随之改变；相同 suffix 不会在不同前缀下重新共享。输出 hash 是合成标识，不与某个服务框架部署的原生 hash ABI 对齐。

- 有 token 身份时，支持任意正整数 `block_size`。
- 只有源 block hash 时，只支持源粒度的正整数倍。例如 Weka 的 64 可以合成 64/128/192/256 等。无法可靠拆分源 hash 来生成 32 或 96 粒度，脚本会报错。
- `hash_id_scope: "local"`（默认）：源身份仅在该 session 内有效。每个抽样实例获得独立命名空间，保留内部复用，避免把重复抽样或不同 session 中相同数字标签变成额外共享。
- `hash_id_scope: "global"`：保留同一数据源内的跨 session 前缀身份。不同命名的数据源有独立命名空间。

请求覆盖范围是参考记录提供的完整上下文。Weka 的 `out` 只是输出长度，没有对应输出内容身份，因此不会凭空为它补 hash；后续请求中已经记录的历史上下文会正常覆盖。

## 测试与真实数据试验

```bash
python3 -m unittest discover -s tests -v
python3 experiments/run_weka.py \
  --source /home/solidyang/workspace/G35/datasets/cc-traces-weka-061326/traces.jsonl \
  --output-dir runs/weka
```

测试包含前缀分叉、重复内容、粒度转换、部分尾块、局部/全局 hash 身份、嵌套时序、混合权重、到达分布、种子复现、强度缩放、边界截断和 CLI。`tests/golden_v1.json` 固定示例输出摘要和前几行，作为版本化回归基线。

真实数据试验生成 0.5×/1×/2× 三份 trace，用独立源数据读取逻辑逐请求核对 block 数、session 内时间、相邻请求的公共前缀长度和截止边界完整性，再逐行比较不同强度下公共请求的身份与时间缩放关系。结果写入 `runs/weka/report.json`。大文件和运行产物均由 `.gitignore` 排除。

请求时序图和分布分析见 [分布报告](experiments/distribution_report.md)。绘图脚本 `experiments/plot_distributions.py` 读取已有试验输出，额外依赖 `experiments/requirements-plot.txt` 中的 Matplotlib 和 NumPy；这些依赖不影响生成器仅使用标准库的运行方式。

读取器只索引源 JSONL 行位置，按需解析被抽中的 session，缓存最近 4 个模板；活跃 session 通过最小堆按请求时间合并输出。内存仍与活跃 session 的模板大小和唯一 block 数有关，源文件在运行期间应保持不变。
