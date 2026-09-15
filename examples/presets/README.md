# 人工参考配置

这些配置表达[设计文档](../../docs/config-driven-refactor.md#7-参考配置)列出的定性负载规律。除各预设注明的 Copilot 与 XPerf 调用数参考、Weka 与 ServeGen 间隔拟合外，均值、CV、长度、比例和峰谷时间均为人工假设。原文的客户端统计不被当成完整 serving 入口分布；各分布独立抽样并不声称还原真实联合分布。

| 配置 | 规律与人工设定 |
|---|---|
| [chat.json](chat.json) | 较少请求，较长轮间时间；历史随轮次增长 |
| [coding_agent.json](coding_agent.json) | 较密集的 LLM 调用，工具结果式外部新增，异质 session 增长倍率 |
| [long_agent.json](long_agent.json) | 20/60/150 次调用，较大新增上下文，混合短间隔与长停顿 |
| [head_clients.json](head_clients.json) | client 排名权重 `1/r^1.6`；集中度作用于 session 发起，不是硬性请求份额 |
| [daily_mixed.json](daily_mixed.json) | 24 小时周期控制点，60 秒强度网格；Chat、Copilot coding 与四类 agent 混合；所有轮数均随机采样 |
| [daily_mixed_2m.json](daily_mixed_2m.json) | 超过 200 万 unique blocks 的独立高负载配置快照；86400 秒、seed=42，实测 2,104,707 个 unique blocks，80 token/s 估算 running 峰值 69 |
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

## daily_mixed 的高负载到达曲线

Session 发起使用 Gamma 分布，整体及所有 task 的 CV=0.7；请求到达仍随机，不使用固定时间间隔。CV 降低用于减少来源时钟的短时起伏，不更改 session 内轮数、增长或 gap 混合分布。

| 时段（相对起点小时） | session 发起强度（sessions/s） |
|---|---|
| 0–2 | 0.141 → 0.1645 → 0.235，分段线性升高 |
| 2–7 | 0.235，持续 5 小时高平台 |
| 7–9 | 0.235 → 0.15275 → 0.235 |
| 9–15 | 0.235，持续 6 小时高平台 |
| 15–17 | 0.235 → 0.15275 → 0.235 |
| 17–22 | 0.235，持续 5 小时高平台 |
| 22–24 | 0.235 → 0.1645 → 0.141 |

保留 86400 秒周期和 60 秒强度积分网格。平台表示 session 发起强度持平，running requests 仍因多轮延迟和随机服务时间波动；不是把并发固定在 64。当前强度用于生成超过 200 万 unique blocks 的 24 小时高负载参考场景。Running 仍按 80 token/s、输出等于下一输入增量且排除末次请求估算，不设置 64 的硬上限。其他 seed 或时长不保证相同 block 数或峰值，运行时不自动调节强度或限流。

## daily_mixed 的会话请求数

Coding 参考 [Agentic Coding in the Wild，Table 4](https://arxiv.org/html/2608.00101v1#S4.SS2)：每 session 的 LLM calls 均值 40.6、中位数 15、P75 42.8、P90 100.5。这是 LLM 调用数，不是用户 turn 数。

使用 `lognormal(mean=40.6, cv=2.5152, min=1, max=2555)`。CV 由 `sqrt((mean/median)^2-1)` 换算，非论文报告值；连续分布的 P90 约 91.5，单一 Lognormal 不会精确满足全部分位数。max=2555 为保留的实验保护上界，不是 Copilot 实测最大值。每个 session 独立抽样并取整，运行时不访问数据集或自动拟合。

Chat 使用 `lognormal(mean=4.2, cv=0.8, min=1, max=30)`，属于人工短会话设定。全部任务均设置 `max_context_tokens=262144`；下一次总输入超限时结束 session，不截断历史。实际计划轮数会低于部分原始抽样轮数；最终还受输出时间窗口限制。task 权重代表 session 发起份额，不是请求份额。

日周期、client 分配和增长/输出长度保持人工设定；coding 轮间间隔使用下述 Weka 基础分布并按 80 token/s 约束缩放，不声称复现 Copilot 的联合分布。当前没有用户 turn 层级、压缩、模型切换或缓存淘汰，历史 block 复用机会不能直接当作论文的实际缓存命中率。

## daily_mixed 的 agent 组成

Coding 的调用数分布、weight=1、clients、长度以及 coding-system 前缀保持 Copilot 调整后的配置，间隔采用按新增量 80 token/s 约束缩放的 Weka 混合分布；Chat 间隔使用 ServeGen 公开子集拟合。整体日周期采用三段高负载平台，具体时段与强度见下文。加入任务会重新归一化各任务权重，因此 coding 的 session 到达频率和最终请求占比会变化。

| task | session 权重 / 份额 | 请求数 mean / CV / max | gap 均值（秒） | 初始私有 / 外部新增 / 输出均值（token） |
|---|---|---|---|---|
| chat | 3 / 30% | 4.2 / 0.8 / 30 | 1637.35（混合期望） | 240 / 100 / 180 |
| coding_agent | 1 / 10% | 40.6 / 2.5152 / 2555 | 616.20（混合期望） | 1800 / 800 / 350 |
| qa_agent | 3 / 30% | 2.4 / 0.8 / 12 | 131.79（混合期望） | 600 / 1500 / 240 |
| customer_service_agent | 1.5 / 15% | 14.9 / 0.45 / 80 | 112.80（混合期望） | 400 / 180 / 160 |
| research_agent | 0.75 / 7.5% | 14.7 / 0.3 / 60 | 829.92（混合期望） | 1200 / 6000 / 700 |
| data_analysis_agent | 0.75 / 7.5% | 18 / 0.85 / 150 | 256.76（混合期望） | 2000 / 1800 / 400 |

权重为人工混合场景，不是市场份额。表中为裁剪前分布参数；整数化、上下界裁剪、256k 上限和结束窗口都会影响实测均值。新增任务的请求数均使用 Lognormal，min=1；daily_mixed 的所有 gap 均为连续 Lognormal 混合，各分量内部随机采样且无上下界裁剪；参数和证据等级见[会话间隔说明](../../docs/task-gap-mixtures.md)。新增任务的增长倍率为 session 级 Lognormal(mean=1, cv=0.35)，每轮新增再独立抽样。

- [XPerf，Table 2 与 §5.2](https://arxiv.org/html/2608.20370v1#S5)：在 gpt-oss-120b 基准实验中，LLMCompiler、Tau-Bench、DeerFlow 每用户任务平均调用数分别为 2.4、14.9、14.7。本配置只引用这些均值和执行结构规律；CV、上限、token 长度、间隔和混合比例都是补充假设，未拟合论文 P95。
- [Agentic AI Workload Characteristics，§4](https://arxiv.org/html/2605.26297v1#S4)：数据分析的轮数、上下文与重试长尾依赖模型和任务。数据分析预设的 mean=18、CV=0.85 是人工参考，不模拟工具失败状态。

新增 task 的 session 表示一个用户任务的线性执行，Copilot coding session 则可涵盖多个用户 turn；这不是统一的产品会话口径。gap 是相邻 LLM 请求到达间隔，不能再叠加工具等待或推理耗时。首末请求时间差也不包含最后一次推理完成时间。

当前不支持 session 内并行分支或汇总 DAG，因此没有加入 ODR/LATS/MagenticOne 的整任务多分支配置，也不把分支调用数累加到一条历史里。短问答仅是线性近似，研究任务明确采用串行结构。四个独立单任务预设保留原来的人工单 Lognormal 间隔，daily_mixed 使用上述混合间隔；独立预设可用前述 CLI 运行（600 秒、0.03 sessions/s）。

## Coding 的 Weka 到达间隔

Coding 的原始参考拟合为以下三分量 Lognormal（均值单位秒）；当前 daily_mixed 的 mean 已乘以 2.5，以满足新增量口径的 80 token/s 约束，详见[参数说明](../../docs/task-gap-mixtures.md)：

| 权重 | mean | CV |
|---|---:|---:|
| 0.630820 | 14.4964 | 0.665310 |
| 0.324353 | 93.1071 | 1.663258 |
| 0.044827 | 4620.7859 | 9.362598 |

来源为 [Weka 061326](https://huggingface.co/datasets/semianalysisai/cc-traces-weka-061326) 的183个筛选后session，取顶层主链相邻请求的 `t[i+1]-t[i]`，共26465个间隔；不合并subagent，不读取traces_modified。它已经包含前次API调用耗时，不再叠加推理/工具时间。数据经过长会话筛选和时间线处理，不能代表完整生产总体。

离线在 log(gap) 上用 EM 比较1/2/3个高斯分量，再转换成算术 mean/CV；按本样本BIC及尾部误差选择3分量。单Lognormal/两分量/三分量的样本内KS（20万模拟样本，seed42）约0.120/0.024/0.009。未做独立测试集验证，不声称泛化精度；分量没有人工/工具原因标签。无30秒上限，也不把跨日长停顿压缩到输出窗口内，末尾请求可能被窗口截断。每个gap独立选分量，尚未拟合session间异质性或gap序列相关性。

复现离线参数估计（仅此脚本需要NumPy；结果不会自动改配置）：

```bash
python3 experiments/fit_weka_gaps.py \
  --input /path/to/cc-traces-weka-061326/traces.jsonl \
  --output runs/weka-gap-fit/models.json
```

原始拟合混合分布总体期望约246秒；当前缩放后约615秒，样本均值约237秒；重尾均值不稳定，主要核对P50/P90/P95/P99以及>=1/5/10/60分钟的比例。独立的旧 `coding_agent.json` 仍为人工示例；本次Weka调整作用于 `daily_mixed.json`。

运行独立的 200 万 block 预设：

```bash
python3 experiments/run_synthetic.py --config examples/presets/daily_mixed_2m.json --output-dir runs/daily-mixed-2m --window 300
```

该预设固定了本次验证配置；改变 seed、时长或分布后，unique blocks 数量和 running 峰值不保证相同。实验 trace、报告和图像保存在忽略的 `runs/` 目录，不随代码提交。
