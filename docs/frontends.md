# 数据集前端与统一输入接口

统一的是文件格式，各数据集独立转换、独立保存，合成时分别读取：

```text
Weka 原始数据    → weka 插件    → weka.jsonl
SwissAI 原始数据 → swissai 插件 → swissai.jsonl
LMSYS 原始数据   → lmsys 插件   → lmsys.jsonl

合成器：按配置权重选择数据集 → 在该数据集中均匀抽取 session → 生成请求到达 trace
```

## 最小统一格式

JSONL 每行一个 session，只包含 `requests`；每条请求只包含 `timestamp` 和 `hash_ids`：

```json
{"requests":[{"timestamp":0,"hash_ids":[10,11]},{"timestamp":3,"hash_ids":[10,11,12]}]}
```

- `timestamp`：session 内相对到达时间，单位秒；前端排序并将首请求归零。
- `hash_ids`：完整 KV block 的有序前缀身份，支持整数或字符串；不足一 block 的尾部由前端剔除。前端可记录空数组，但后端会在采样前剔除这类请求；全空 session 不参与合成。保留请求间隔不变，首个有效请求重新归零。
- 一行就是一个 session，无需输入 session ID；合成时生成 `session_id`，来源通过文件和行号定位。

输入不含 token 数、token 序列、schema 版本、来源信息、身份命名空间或作用域。后端严格验证这两个层级的字段，不再兼容旧的富字段 session JSONL。

所有输入需按同一目标 block 粒度转换。三个内置前端均支持 `--block-size`，默认 128；它必须与合成配置的 `block_size` 相同。token 化、源 bucket 合并和前缀 hash 编码都在前端完成，后端不再重新划分 block。仅有源 block 身份时，目标粒度必须是源粒度的正整数倍。

`prepare.py` 每次只转换一个数据集，多个 `--input` 文件表示该数据集的分片。配比只在合成配置中设置，调整配比无需重新转换。修改 block size 则需重新转换。

转换另存 `.manifest.json`，记录转换参数、输入输出指纹和时间来源等运行信息，不增加 session 字段。后端发现该文件时核对指纹和 block size；手工提供的最小 JSONL 无需附带 manifest，其 block size 由合成配置声明，也可用 `datasets[].block_size` 显式校验。转换失败不会替换已有输出。

## Weka

```bash
python3 prepare.py weka --input /path/to/weka/traces.jsonl \
  --output runs/normalized/weka.jsonl --block-size 128
python3 generate.py --config examples/weka.json --output runs/weka.jsonl
```

递归展开 subagent，保留源 `t` 的相对时序，用 `in` 校验完整源 block 覆盖，并合并为目标粒度。内层 `t` 已相对于外层 session，不叠加容器时间。`in` 仍是源长度代理，不变成精确 tokenizer 计数。原来的生成配置 `format: "weka"` 需要迁移；后端不再读取它。

## SwissAI

```bash
python3 prepare.py swissai --input /path/to/qwen3-32b-buckets.jsonl \
  --output runs/normalized/swissai.jsonl \
  --session-mode window --window-seconds 300 --bucket-size 16 --block-size 128
```

使用有 `token_count`、`bucket_ids`、`created_at` 的子集。官方 Qwen3-32B 子集的 bucket 为 16 tokens，包含右 padding；插件根据精确长度剔除不完整尾 bucket。仅有 `total_buckets/reused_buckets` 的旧文件会报错，不猜测尾部；没有 bucket 身份的主 `trace.jsonl` 也不能用于该前端。

插件用临时 SQLite 排序，允许源记录乱序，相同时间保持原始行顺序；无时区后缀的日期按 UTC 处理。必须明确选择分组方式：

- `--session-mode window`：UTC 对齐的固定时间窗口，保留窗口内请求间隔；窗口不是实际用户会话，分组方式记录在转换报告中。
- `--session-mode field --session-field session_id`：要求输入确实有该字段，不从 request ID 或前缀相似度猜测会话。

前端将文件指纹和模型直接编码进 hash，避免不同来源的同名 bucket 误共享，JSONL 不需要额外的 namespace 字段。`--model` 可以筛选模型，也可给缺失模型字段的文件提供模型名。

如需保留 SwissAI 同一文件、同一模型内的跨窗口共享，在该数据集的合成配置中设置 `hash_id_scope: "global"` 和 `new_block_jitter: 0`（见示例）。作用域是合成策略，不是输入字段；默认 `local` 隔离每个抽样实例。独立扰动各窗口会破坏共享身份，因此后端拒绝这样处理。重复抽到窗口仍使用相同 global 身份，代表重访源内容。

## LMSYS-Chat-1M

```bash
python3 -m venv .venv-frontends
.venv-frontends/bin/python -m pip install -r requirements-frontends.txt
.venv-frontends/bin/python prepare.py lmsys \
  --input /path/to/lmsys/data/*.parquet --output runs/normalized/lmsys.jsonl \
  --tokenizer Qwen/Qwen3-0.6B --block-size 128 \
  --turn-interval-mean 30 --turn-interval-cv 1 --seed 42
```

也支持本地 JSONL 和 `.jsonl.gz`。原始行包含 `conversation_id` 和 `conversation`，消息为 `{role, content}`。`--model`、`--language` 筛选源标签，`--tokenizer` 指定目标 token 化规则，`--revision` 可固定 tokenizer 版本。

每个已记录 assistant 回复对应一条输入请求：包含此前消息和当前 user 消息，用目标 tokenizer 的 chat template 加 generation prompt 编码，再划分完整 block 并生成前缀 hash；当前回复不进入本次输入，但会进入后续轮次。消息按 user/assistant 交替，没有已记录回复的尾部 user 消息不额外构造请求。

LMSYS 没有逐轮时间：首轮为 0，后续间隔合成自 Gamma 分布，默认均值 30 秒、CV 1；CV 0 为固定间隔。**这不是 LMSYS 实测时序或执行完成时间。** 每个 conversation 是独立的 local session。

原始 LMSYS 仓库需要接受访问条件并授权下载；插件读取本地文件，不绕过访问限制。`examples/data/lmsys.fixture.jsonl` 是人工测试样例，不能用来宣称真实 LMSYS 分布。

## 合成阶段按数据集配比采样

```bash
python3 generate.py --config examples/mixed.json --output runs/mixed_3h.jsonl
```

配置分别指定两份独立输入，示例 [examples/mixed.json](../examples/mixed.json) 为：

```json
{"datasets": [
  {"name":"swissai","path":"../runs/normalized/swissai.jsonl","weight":0.5,"hash_id_scope":"global","new_block_jitter":0},
  {"name":"lmsys","path":"../runs/normalized/lmsys.jsonl","weight":0.5}
]}
```

每次启动新 session，先按 `weight / sum(weights)` 选择来源，再从该来源的独立索引均匀抽取一行。数据集大小不额外乘进权重，也不预先合成一个数据池。两份输入的目标 block size 都是 128。各 `datasets[]` 项可覆盖 `new_block_jitter`：SwissAI global 使用 0，LMSYS 继承顶层 0.3。转换只做一次，调权重不需要重新 tokenize。

`weight` 是**有效采样单元的概率权重**，不是请求数或 block 数比例。需要最终请求数比例时，使用 `experiments/run_mixed.py --ratios ...` 校准后生成，详见 README；空请求不参与配比计算。manifest 的 `source_mix` 记录每个来源实际单元数、请求数和 block 引用数。含有 `request_window` 时，`max_concurrent_sessions` 限制同时活跃的采样单元，不能解释为 SwissAI 的真实用户会话并发。

## 自定义插件

实现两个方法，后端不用改动：

```python
class MyFrontend:
    def configure(self, parser):
        parser.add_argument("--my-option", default="example")

    def convert(self, records, options, context):
        for row in records:
            # row.data 是原始对象，file_index / row_index 是来源索引。
            # 前端已完成目标 block 划分及前缀 hash 编码。
            yield {"requests": [{"timestamp": 0, "hash_ids": row.data["complete_prefix_hashes"]}]}
```

```bash
PYTHONPATH=/path/to/plugins python3 prepare.py my_plugin:MyFrontend \
  --input raw.jsonl --output normalized.jsonl --my-option example
```

内置插件在 `tracegen/frontends/__init__.py` 注册。公共读取器提供 JSONL/gzip/Parquet 和 `--limit`；前端实现源语义，统一验证与原子输出由 `prepare.py` 负责。`prepare.py <frontend> --help` 展示插件参数。

字段依据：[SwissAI 数据卡](https://huggingface.co/datasets/eth-easl/swissai-serving-trace)、[LMSYS 数据卡](https://huggingface.co/datasets/lmsys/lmsys-chat-1m)、[Transformers chat template 文档](https://huggingface.co/docs/transformers/en/chat_templating)。
