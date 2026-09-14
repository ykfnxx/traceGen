# 人工参考配置

这些配置表达[设计文档](../../docs/config-driven-refactor.md#7-参考配置)列出的定性负载规律。所有均值、CV、长度、比例和峰谷时间都是人工假设，**没有复制论文实测数值，也没有拟合公开数据**。原文的客户端统计不被当成完整 serving 入口分布；各分布独立抽样并不声称还原真实联合分布。

| 配置 | 规律与人工设定 |
|---|---|
| [chat.json](chat.json) | 较少请求，较长轮间时间；历史随轮次增长 |
| [coding_agent.json](coding_agent.json) | 较密集的 LLM 调用，工具结果式外部新增，异质 session 增长倍率 |
| [long_agent.json](long_agent.json) | 20/60/150 次调用，较大新增上下文，混合短间隔与长停顿 |
| [head_clients.json](head_clients.json) | client 排名权重 `1/r^1.6`；集中度作用于 session 发起，不是硬性请求份额 |
| [daily_mixed.json](daily_mixed.json) | 24 小时周期控制点，60 秒强度网格；任务权重固定 |
| [burst_mixed.json](burst_mixed.json) | 整体峰谷与全局 ramp burst；已有 session 自然产生拖尾 |

Client 分解和异质性参考 ServeGen 的思路；任务属性差异、Agent 多轮与前缀复用、工具/用户停顿分别参考设计文档中的 FineServe、SMetric、Agentic Coding in the Wild 与 TraceLab；日周期是说明配置能力的人工场景。具体观测范围与单位应回到原论文核对，本目录不提供生产测量背书。

运行任意预设：

```bash
python3 experiments/run_synthetic.py --config examples/presets/coding_agent.json \
  --output-dir runs/coding-agent --no-plots
```

在保持 seed 的情况下修改轮数、增长倍率或流量控制点，并比较独立输出目录。修改图表 `--window` 不改变 trace；修改 `traffic.resolution` 属于生成参数，会改变积分近似与到达时间。
