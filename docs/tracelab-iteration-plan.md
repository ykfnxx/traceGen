# TraceGen 迭代计划：TraceLab 整段采样与 Agent 轮次标签

状态：已实现。本文记录本次迭代的最终规则与验收要求。现有分布合成继续使用；新增 TraceLab coding session 采样入口，共用 client pool、流量配置和 trace 输出流程。

## 1. 交付范围

1. 增加从 TraceLab 采样整段 coding session 的生成方式。
2. 每条输出请求必须包含 `current_user_message_count` 和 `is_agent_turn_end`。
3. 其他生成方式使用统一默认值，不推测用户交互结构。

只增加一个 TraceLab 读取模块和一个 session 生成分支，不建设通用数据源插件、拟合器、自动调参流程或新 Web 功能。新输出格式直接替换旧格式，不提供旧字段别名、旧格式导出或读取时自动补字段。

## 2. 新请求字段

| 字段 | 类型 | TraceLab 采样 | 其他生成方式默认值 |
|---|---|---|---|
| `current_user_message_count` | 非负整数或 null | 原样保留当前 LLM 请求新增的用户消息数量 | `null` |
| `is_agent_turn_end` | boolean 或 null | 标记一次用户交互所触发的 Agent 执行轮次的最后一条 LLM 请求 | `null` |

`null` 表示没有这项信息；不能用 `0` 或 `false` 代替未知，否则会将未建模的请求误认为已知的轮次内部请求。`current_user_message_count` 不是累计用户轮次编号。本次不新增 `user_turn_index`，不使用 `is_user_turn_end`。

在完整源 session 上，按请求顺序计算 `is_agent_turn_end`：

- 下一条请求的 `current_user_message_count > 0`：当前请求为 `true`。
- 下一条请求的 `current_user_message_count == 0`：当前请求为 `false`。
- 源 session 最后一条请求：当前支持的字段不足以证明自然结束，填 `null`。不额外增加终止事件识别机制。

该标签表示观测到的 Agent 执行轮次边界，不表示任务成功、模型停止原因或整个 session 结束。计数大于 1 时保留原值，仍只识别一次边界，不拆分 LLM 请求。

| request_index | current_user_message_count | is_agent_turn_end |
|---:|---:|---|
| 0 | 1 | false |
| 1 | 0 | true |
| 2 | 1 | false |
| 3 | 0 | null |

先在完整源序列上生成标签，再做时间窗口截断。不能因为某条请求成为导出文件里的末次请求而改写标签。`output.request_metadata=false` 时也必须保留这两个字段，它们属于新 trace 的基础字段。

## 3. TraceLab session 采样

### 数据入口和筛选

在 task 上增加显式的 `source`，指定 `type: tracelab` 和本地 JSONL/JSONL.gz 文件路径；路径相对配置文件解析。未指定采样来源的 task 继续按现有分布生成。两种 task 可以放进同一整体配置。

TraceLab task 的 `session` 只接受 `max_context_tokens`，不同时接受请求数、gap、输入增长或输出长度分布，也不接受额外公共前缀配置，避免覆盖样本或重复增加输入。整体权重、client 数量和流量参数沿用现有接口。

读取模块负责：

1. 按 `(provider, session_id)` 分组，按 `round_index` 排序。
2. 检查序号唯一、输入/输出 token 非负整数、用户消息数量与事件一致。
3. 提取相对时间，排除无法取得锚点或锚点倒退的整段 session。
4. 若配置总输入上限，任何一条请求超限即排除整个源 session，不裁剪 token 或删除中间请求。
5. 派生 Agent 轮次结束标签，形成内存中的 session 模板池。

同一源文件在一次生成中只解析一次。不自动联网下载，不额外引入 DuckDB 依赖或持久化中间格式；没有可用 session 时明确报错。筛选规则、排除数量和源文件 SHA256 写入 manifest。

### 时间规则

对请求 i，从 `timing_events` 取首次 `reasoning/text/tool_call` 之前或同时发生的最后一个 `user_message/tool_result` 时间，记为 a_i：

```text
relative_time[i] = a_i - a_0
output_timestamp[i] = generated_session_start + relative_time[i]
```

时间口径是客户端输入就绪代理，不是服务端接收实测。要求每条请求可取得锚点、相对时间非递减；相同时间保留。缺失时间不补齐，间隔不重新采样，不叠加工具或推理时间，不按 token/s 拉伸，不压缩跨日停顿。

### 抽样和生成

Client pool 每次发起 session 时，用既有 session seed 派生独立的采样随机流，在模板池中按 session 等概率、有放回抽取一段。模板池按稳定来源标识排序；选择模板不消耗 client 到达时钟的随机流。

保留源 session 的请求数量、顺序、相对时间、输入和输出长度、用户消息数量及派生标签。不同生成 session 使用新的 session ID 和私有 block 身份，即使抽中相同模板也不共享私有历史。

采样模式直接使用源 `input_tokens_total` 和 `output_tokens`，不调用现有增长公式。不能将 `newly_append_tokens` 直接解释为真实外部新增，也不能把输入长度差全部叫作模型输出；无法可靠取得的 `external_tokens` 填 `null`，统计模块跳过未知值。

为保留来源间真实长度变化，block 合成采用简单且明确的近似：输入非递减时继承已有完整 block 并追加；输入缩短时重建该 session 的私有前缀。重建后的身份使用新代次，不能再次长到相同长度就复用已丢弃的旧 hash。它不是源 trace 的真实内容前缀，不使用 `prefix_tokens` 缓存读量冒充内容公共前缀，也不额外合成跨 session 共享。

整体配置只改变新 session 发起时间和数量。输出窗口依旧采用 `[0, duration)`，只截断窗口外后缀。manifest 记录源 provider/session、计划与实际输出请求数、窗口截断，以及实际使用的源 session 数和重复采样次数。

## 4. 数据集中的 Session 元数据

沿用数据集旁的 `trace.jsonl.manifest.json`，在 `sessions` 中记录每个实际输出的 session，不新增独立元数据文件。所有生成方式都遵守同一规则：请求行通过 `session_id` 关联到唯一的 session 元数据，元数据中的 `task` 必须等于配置中的 task key，例如 `coding_agent`、`chat`、`research_agent`。

| 字段 | 含义 |
|---|---|
| `session_id` | 生成后的 session 唯一标识，与请求行一致 |
| `task` | 所属任务类型，取配置中的 task key |
| `client` | 发起该 session 的 client 标识 |
| `session_seed` | 该 session 的随机种子，用于复现 |
| `start`、`end` | 生成计划中的首末请求到达时间；`end` 可能超过输出窗口 |
| `planned_requests`、`emitted_requests` | 计划请求数和实际输出请求数，用于识别窗口截断 |

TraceLab 采样 session 额外记录源 `provider` 和源 `session_id`，与生成后的标识明确区分；文件来源及 SHA256 在 manifest 的数据源信息中记录。`task` 由生成配置决定，不由 provider 或源 session ID 推断。首末请求时间不表示推理开始或完成时间。

继续复用现有 `sessions` 列表，不在每条请求中重复写 task，也不增加单独的 task 映射表。分析时通过 session 元数据按 task 分组，避免通过解析 session ID 推测任务类型。

## 5. 实现顺序

本迭代按以下顺序实现，各项共享同一输出契约。

1. **读取与筛选**：新增 TraceLab 模块，统一封装已验证的分组、时间锚点、上限过滤和轮次标签规则。
2. **接入生成**：task 来源分支复用 client pool；实现稳定整段抽样、时间平移、上下文缩减后的 hash 重建和来源 manifest。统一维护每个输出 session 的元数据，显式记录 task 归属。
3. **统一新格式**：所有输出路径固定写两个字段；其他生成方式填 `null`；统计、CLI 和工作台消费者按新格式读取，不自动兼容缺字段旧 trace。
4. **更新分析**：根据当前请求是否带新用户消息，将进入该请求的 gap 分为用户边界间隔和轮次内部间隔；未知标签单列，不混入任何一类。修正原有仅适用于追加历史的前缀统计，按实际 hash 关系计算，支持空 block 列表及未知外部新增。
5. **交付验证与文档**：增加一个 TraceLab coding 示例配置、读取/生成用法和小型测试；同步 AGENTS、设计文档及配置说明中“禁止数据集采样”的旧约束。现有 daily_mixed 不自动改成采样模式。

配置协议沿用当前任务配置结构；不为了增加两列引入多版本框架。实现时直接更新输出契约和格式说明，消费者要求新字段存在。协议要求有字段、字段为 `null` 与兼容旧格式缺字段是不同情况。

## 6. 验证和验收

- 使用小型人工 TraceLab fixture 验证跨用户轮次、单请求、最后一条未知标签、字段与事件一致性、相同时间和时间倒退。
- 验证采样后每条记录与源模板的长度和标签一致，时间仅平移；相同 seed 可复现，采样不改变 client 发起时钟。
- 重复抽中模板的不同生成 session 私有 hash 隔离；上下文缩减、后续增长和不足完整 block 的情况正确。
- 验证窗口截断保留源标签；其他生成方式两个字段均为 `null`，包括关闭可选请求元数据的输出。
- 验证每条请求的 `session_id` 在 manifest 中恰好对应一条 session 元数据，`task` 与生成配置一致；不同 task 混合、重复采样同一源 session 及窗口截断时均保持正确归属。按元数据聚合的请求数应与 task 汇总一致。
- 验证统计对未知标签/外部新增、上下文缩减的处理；旧 trace 缺字段时报错，不自动补齐。
- 对固定 TraceLab v0.0.2 做一次真实读取和短 trace 生成，检查筛选规模、相对时间、来源复现、抽样覆盖率及统计图。最后执行仓库现有测试。

本次预检查可作为验收基线：原始 8,058 sessions / 665,453 LLM 调用；当前时间规则后 6,762 / 390,758；再按所有请求输入不超过 262,144 tokens 筛选后 **6,433 / 246,993**，其中 Claude 4,900 / 126,404，Codex 1,533 / 120,589。可用集合有 2,060 个多用户交互 session。该规则会排除部分 Codex 事件布局，不声称它们原始数据损坏，本次不扩展时间修复规则。

来源：[TraceLab v0.0.2](https://github.com/uw-syfi/TraceLab/releases/tag/v0.0.2)。压缩 JSONL SHA256：`11ce51ec0a25e3d1d95b025bca2f7d1647e47571eb7cc968acd5fc64d4b4fb65`。本地预检查产物在 `runs/tracelab-screen-v002/`，实验数据不纳入代码提交。

## 7. 实现验证结果

现有与新增测试共 23 项通过。真实 v0.0.2 筛选得到 6,433 个 session / 246,993 次调用，与预检查一致；600 秒、seed=42、0.03 sessions/s、3 clients 的验证输出 8 个 session、83 条请求，6 个 session 被窗口截断，PNG/SVG 正常生成。结果位于 `runs/tracelab-iteration-smoke/output/`，不纳入代码提交。短窗口结果用于接口与规则验证，不代表源分布复现精度。
