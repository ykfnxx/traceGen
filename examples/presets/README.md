# 人工参考配置

这些配置表达[设计文档](../../docs/config-driven-refactor.md#7-参考配置)列出的定性负载规律。除 `daily_mixed.json` 中注明的 Weka 主 agent 请求数参数外，均值、CV、长度、比例和峰谷时间均为人工假设。原文的客户端统计不被当成完整 serving 入口分布；各分布独立抽样并不声称还原真实联合分布。

| 配置 | 规律与人工设定 |
|---|---|
| [chat.json](chat.json) | 较少请求，较长轮间时间；历史随轮次增长 |
| [coding_agent.json](coding_agent.json) | 较密集的 LLM 调用，工具结果式外部新增，异质 session 增长倍率 |
| [long_agent.json](long_agent.json) | 20/60/150 次调用，较大新增上下文，混合短间隔与长停顿 |
| [head_clients.json](head_clients.json) | client 排名权重 `1/r^1.6`；集中度作用于 session 发起，不是硬性请求份额 |
| [daily_mixed.json](daily_mixed.json) | 24 小时周期控制点，60 秒强度网格；Chat 与 coding 均使用随机整数轮数，coding 为 Weka 主 agent 参考长尾 |
| [burst_mixed.json](burst_mixed.json) | 整体峰谷与全局 ramp burst；已有 session 自然产生拖尾 |

Client 分解和异质性参考 ServeGen 的思路；任务属性差异、Agent 多轮与前缀复用、工具/用户停顿分别参考设计文档中的 FineServe、SMetric、Agentic Coding in the Wild 与 TraceLab；日周期是说明配置能力的人工场景。具体观测范围与单位应回到原论文核对，本目录不提供生产测量背书。

运行任意预设：

```bash
python3 experiments/run_synthetic.py --config examples/presets/coding_agent.json \
  --output-dir runs/coding-agent --no-plots
```

在保持 seed 的情况下修改轮数、增长倍率或流量控制点，并比较独立输出目录。修改图表 `--window` 不改变 trace；修改 `traffic.resolution` 属于生成参数，会改变积分近似与到达时间。

## daily_mixed 的会话请求数

参考本地原始文件 `G35/datasets/cc-traces-weka-061326/traces.jsonl`（未使用 `traces_modified.jsonl`），对应 [Weka 061326 数据集](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-061326)。逐行统计顶层模型请求，排除包含 `requests` 的 subagent 分组，得到 183 条主 agent 流、26,648 次请求。请求数均值 145.6175、总体 CV 2.1205、P50 58、P95 412.8，范围 2–2555。发布过滤条件针对完整 session，不意味着主 agent 请求数至少为 20。

Coding 使用 `lognormal(mean=145.62, cv=2.121, min=1, max=2555)`。mean/CV 由上述样本矩直接换算并取有限小数，max 取观测最大值作为实验上界；并非迭代拟合。每个 session 独立抽样后取整，能生成上界内各整数，不再局限三档。生成时仅使用配置，不读取 Weka。Weka 为经过筛选的 coding session 样本，不能代表所有 coding 流量；subagent 分支没有拼接到当前线性上下文中。

固定 seed=42 对该请求数分布直接抽样 100,000 次：均值约 141.94、P50 62、P95 535，得到 1,796 种不同整数请求数。clip 会改变均值并在上界产生少量质量；单一 Lognormal 仅近似长尾形状，未同时匹配全部分位数。

Chat 使用 `lognormal(mean=4.2, cv=0.8, min=1, max=30)`，仍是人工短会话设定。两个任务都设置 `session.max_context_tokens=262144`，总输入包含公共前缀、历史和当前新增；下一请求将超限时结束 session。当前输出会在下一轮计入历史，本轮输出本身不属于当前输入上限。原始抽样轮数仍保留 Weka 参考长尾，但实际计划轮数会受上下文预算缩短，因此上面的轮数抽样统计不等于最终 trace 的轮数统计。

任务权重和发起强度不变，原示例约 4.3 万请求/天的估算不再适用；实际总请求数还受上下文限制和时间窗口影响。本次仅验证 session 计划及边界，没有运行完整一天的 trace。当前不模拟 Weka 的压缩、重置或分支。
