# 人工参考配置

这些配置表达[设计文档](../../docs/config-driven-refactor.md#7-参考配置)列出的定性负载规律。除各预设注明的 Copilot 与 XPerf 调用数参考外，均值、CV、长度、比例和峰谷时间均为人工假设。原文的客户端统计不被当成完整 serving 入口分布；各分布独立抽样并不声称还原真实联合分布。

| 配置 | 规律与人工设定 |
|---|---|
| [chat.json](chat.json) | 较少请求，较长轮间时间；历史随轮次增长 |
| [coding_agent.json](coding_agent.json) | 较密集的 LLM 调用，工具结果式外部新增，异质 session 增长倍率 |
| [long_agent.json](long_agent.json) | 20/60/150 次调用，较大新增上下文，混合短间隔与长停顿 |
| [head_clients.json](head_clients.json) | client 排名权重 `1/r^1.6`；集中度作用于 session 发起，不是硬性请求份额 |
| [daily_mixed.json](daily_mixed.json) | 24 小时周期控制点，60 秒强度网格；Chat、Copilot coding 与四类 agent 混合；所有轮数均随机采样 |
| [qa_agent.json](qa_agent.json) | LLMCompiler 式短流程问答的线性近似，少量调用 |
| [customer_service_agent.json](customer_service_agent.json) | Tau-Bench 式客服查询和操作，小幅上下文增长 |
| [research_agent.json](research_agent.json) | DeerFlow 式串行研究，较大的外部新增上下文 |
| [data_analysis_agent.json](data_analysis_agent.json) | 数据分析工具循环，随机轮数与长尾间隔 |
| [burst_mixed.json](burst_mixed.json) | 整体峰谷与全局 ramp burst；已有 session 自然产生拖尾 |

Client 分解和异质性参考 ServeGen 的思路；任务属性差异、Agent 多轮与前缀复用、工具/用户停顿分别参考设计文档中的 FineServe、SMetric、Agentic Coding in the Wild 与 TraceLab；日周期是说明配置能力的人工场景。具体观测范围与单位应回到原论文核对，本目录不提供生产测量背书。

运行任意预设：

```bash
python3 experiments/run_synthetic.py --config examples/presets/coding_agent.json \
  --output-dir runs/coding-agent --no-plots
```

在保持 seed 的情况下修改轮数、增长倍率或流量控制点，并比较独立输出目录。修改图表 `--window` 不改变 trace；修改 `traffic.resolution` 属于生成参数，会改变积分近似与到达时间。

## daily_mixed 的会话请求数

Coding 参考 [Agentic Coding in the Wild，Table 4](https://arxiv.org/html/2608.00101v1#S4.SS2)：每 session 的 LLM calls 均值 40.6、中位数 15、P75 42.8、P90 100.5。这是 LLM 调用数，不是用户 turn 数。

使用 `lognormal(mean=40.6, cv=2.5152, min=1, max=2555)`。CV 由 `sqrt((mean/median)^2-1)` 换算，非论文报告值；连续分布的 P90 约 91.5，单一 Lognormal 不会精确满足全部分位数。max=2555 为保留的实验保护上界，不是 Copilot 实测最大值。每个 session 独立抽样并取整，运行时不访问数据集或自动拟合。

Chat 使用 `lognormal(mean=4.2, cv=0.8, min=1, max=30)`，属于人工短会话设定。全部任务均设置 `max_context_tokens=262144`；下一次总输入超限时结束 session，不截断历史。实际计划轮数会低于部分原始抽样轮数；最终还受输出时间窗口限制。task 权重代表 session 发起份额，不是请求份额。

日周期、client 分配、轮间间隔和增长/输出长度保持人工设定，不声称复现 Copilot 的联合分布。当前没有用户 turn 层级、压缩、模型切换或缓存淘汰，历史 block 复用机会不能直接当作论文的实际缓存命中率。

## daily_mixed 的 agent 组成

Coding 的整个 task（包括 weight=1、clients、长度和间隔）以及 coding-system 前缀保持 Copilot 调整后的配置；Chat 也保持原配置。整体日周期和 session 发起强度保持原值。加入任务会重新归一化各任务权重，因此 coding 的 session 到达频率和最终请求占比会变化。

| task | session 权重 / 份额 | 请求数 mean / CV / max | gap 均值（秒） | 初始私有 / 外部新增 / 输出均值（token） |
|---|---|---|---|---|
| chat | 3 / 30% | 4.2 / 0.8 / 30 | 12 | 240 / 100 / 180 |
| coding_agent | 1 / 10% | 40.6 / 2.5152 / 2555 | 2 | 1800 / 800 / 350 |
| qa_agent | 3 / 30% | 2.4 / 0.8 / 12 | 2 | 600 / 1500 / 240 |
| customer_service_agent | 1.5 / 15% | 14.9 / 0.45 / 80 | 6 | 400 / 180 / 160 |
| research_agent | 0.75 / 7.5% | 14.7 / 0.3 / 60 | 12 | 1200 / 6000 / 700 |
| data_analysis_agent | 0.75 / 7.5% | 18 / 0.85 / 150 | 8 | 2000 / 1800 / 400 |

权重为人工混合场景，不是市场份额。表中为裁剪前分布参数；整数化、上下界裁剪、256k 上限和结束窗口都会影响实测均值。新增任务的请求数均使用 Lognormal，min=1；gap 也是连续 Lognormal，不用几个固定间隔代替随机性。新增任务的增长倍率为 session 级 Lognormal(mean=1, cv=0.35)，每轮新增再独立抽样。

- [XPerf，Table 2 与 §5.2](https://arxiv.org/html/2608.20370v1#S5)：在 gpt-oss-120b 基准实验中，LLMCompiler、Tau-Bench、DeerFlow 每用户任务平均调用数分别为 2.4、14.9、14.7。本配置只引用这些均值和执行结构规律；CV、上限、token 长度、间隔和混合比例都是补充假设，未拟合论文 P95。
- [Agentic AI Workload Characteristics，§4](https://arxiv.org/html/2605.26297v1#S4)：数据分析的轮数、上下文与重试长尾依赖模型和任务。数据分析预设的 mean=18、CV=0.85 是人工参考，不模拟工具失败状态。

新增 task 的 session 表示一个用户任务的线性执行，Copilot coding session 则可涵盖多个用户 turn；这不是统一的产品会话口径。gap 是相邻 LLM 请求到达间隔，不能再叠加工具等待或推理耗时。首末请求时间差也不包含最后一次推理完成时间。

当前不支持 session 内并行分支或汇总 DAG，因此没有加入 ODR/LATS/MagenticOne 的整任务多分支配置，也不把分支调用数累加到一条历史里。短问答仅是线性近似，研究任务明确采用串行结构。四个新增单任务预设与 daily_mixed 中对应 task 完全相同，可用前述 CLI 独立运行（600 秒、0.03 sessions/s）。
