# 近似等 unique blocks 的八类场景

八份配置均生成 86400 秒 trace，目标为原 `daily-mixed-2m-unscaled` 的 2,251,510 个 unique blocks，容差 ±2%。每个 block 为 128 token，seed=42。沿用 `daily_mixed_2m.json` 的三段高平台曲线形状、Gamma CV=0.7 发起时钟；只对整体 session 发起强度乘不同的常数。不要求相同 RPM、session 数或总 block 访问量。

| 配置 | 任务组成（session 权重） |
|---|---|
| `chat.json` | chat |
| `qa_agent.json` | qa_agent；与此前名为 pure_chat 的数据集属于同一任务类型 |
| `customer_service_agent.json` | customer_service_agent |
| `research_agent.json` | research_agent |
| `data_analysis_agent.json` | data_analysis_agent |
| `coding_agent.json` | coding_agent，TraceLab 整段采样 |
| `interactive_mixed.json` | chat : customer_service_agent : qa_agent = 1 : 1 : 1 |
| `agent_mixed.json` | coding_agent : research_agent : data_analysis_agent = 1 : 1 : 1 |

混合权重是人工实验变量，不代表市场份额或请求份额。分布任务完整沿用 daily_mixed_2m 的 session profile、client 和公共前缀规则；不是旧版独立任务预设。Coding 沿用纯 TraceLab coding 的 4 client 配置，保留整段 session 的相对时间、长度和标签。不同类型的 session 口径不同；coding 可包含多个用户轮次。

Coding 源文件需放在 `runs/tracelab-screen-v002/syfi_coding_trace.jsonl.gz`，或修改两份含 coding 配置中的 `source.path`。相对路径按配置文件目录解析。源文件获取及筛选规则见 [配置文档](../../../docs/configuration.md#tracelab-整段采样)。

从仓库根目录生成，例如：

```bash
python3 experiments/run_synthetic.py \
  --config examples/presets/task_scenarios_2p25m/chat.json \
  --output-dir runs/task-scenarios-2p25m/chat \
  --window 300 --no-plots
```

将两个路径中的 `chat` 替换成表中的配置名即可生成其他场景。每份输出包含 trace.jsonl、trace.jsonl.manifest.json、config.json、report.json。当前实验总表位于忽略目录 `runs/task-scenarios-2p25m/`。

这是固定参数的一组实验配置，生成器不自动追踪 unique block 目标。修改 seed、时长、block_size 或源文件后须重新检查实际数量。日末会截断后续请求；不通过压缩 session 内间隔或删除已生成请求凑目标。

比较缓存时要同时报告 RPM、总 block 访问量、复用间隔与上下文长度；unique block 总量相近不表示工作集时间分布或瞬时缓存压力相同。历史前缀复用比例不是有限容量缓存命中率。
