# 八类场景：三倍到达强度

基于 [task_scenarios_2p25m](../task_scenarios_2p25m/README.md) 的八份配置，仅将 `traffic.session_rate.points` 中每个强度值乘以 3。仍为 24 小时、seed=42、128 token/block；任务权重、client、session 轮数、增长、间隔、前缀规则和 TraceLab 采样方式均保持不变。

文件名对应：chat、qa_agent、customer_service_agent、research_agent、data_analysis_agent、coding_agent、interactive_mixed、agent_mixed。

预期平均 RPM 约为对应基线的三倍，unique blocks 也预计增加到约三倍规模。配置控制的是新 session 发起强度，实际数量受随机采样和日末截断影响；本组未生成实测数据，不保证严格三倍 RPM，也不保证新的 unique blocks 偏差在 ±2% 内。

在仓库根目录执行以下内容，也可保存为 Bash 脚本运行：

```bash
#!/usr/bin/env bash
set -euo pipefail

for scenario in chat qa_agent customer_service_agent research_agent \
  data_analysis_agent coding_agent interactive_mixed agent_mixed
do
  python3 experiments/run_synthetic.py \
    --config "examples/presets/task_scenarios_2p25m_3x_rpm/${scenario}.json" \
    --output-dir "runs/task-scenarios-2p25m-3x-rpm/${scenario}" \
    --window 300 --no-plots
done
```

含 Coding 的两份配置需要 `runs/tracelab-screen-v002/syfi_coding_trace.jsonl.gz`；源文件准备见 [配置说明](../../../docs/configuration.md#tracelab-整段采样)。每个场景输出 trace、manifest、配置快照与统计报告。重复运行会覆盖对应输出目录中的生成文件。
