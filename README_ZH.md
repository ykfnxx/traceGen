# traceGen

[English](README.md) | 简体中文

traceGen 根据任务配置合成 LLM serving 接收的多轮请求流。新入口不需要数据集：整体配置控制 **session 发起强度**、任务组成、波峰波谷和 burst；client 独立发起 session；每个 session 根据独立 seed 采样轮数、上下文增长和请求间隔，最后按到达时间归并。

输出表达请求时序和合成前缀关系，不包含真实文本、推理执行或缓存容量模型。参考配置的数字均为人工示例，论文用于提供规律，不进行自动校准。

## 快速运行

Python 3.10+，生成和 JSON 统计仅依赖标准库。从仓库根目录执行：

```bash
python3 generate.py --config examples/config.example.json --output runs/example/trace.jsonl
python3 experiments/run_synthetic.py --config examples/config.example.json \
  --output-dir runs/example-report --window 10 --no-plots
```

需要 PNG/SVG 曲线时安装可选依赖，再去掉 `--no-plots`：

```bash
python3 -m venv .venv
.venv/bin/pip install -r experiments/requirements-plot.txt
.venv/bin/python experiments/run_synthetic.py --config examples/config.example.json \
  --output-dir runs/example-report --window 10
```

`--window` 只改变统计窗口。生成同一配置得到相同 trace；可分别使用 1、10、60 秒窗口观察尖峰和趋势。

## 交互工作台

```bash
npm --prefix web ci
npm --prefix web run build
python3 preview.py
```

打开 http://127.0.0.1:8765 。构建前端需要 Node.js 22+，运行服务只需要 Python；浏览器资源本地提供，不依赖 CDN。`--port` 可改端口，`--output-dir` 可改运行文件目录。

- 整体、任务、前缀和 JSON 面板覆盖完整生成配置，提供六组参考预设。
- 拖动全局/task/client 曲线控制点、burst 起止和倍率；松开后自动生成，输入框修改会延迟合并刷新。
- 固定/离散/Gamma/Lognormal 分布有独立种子的抽样 CDF 与 PDF/概率质量预览；不消耗 session 随机流。
- 固定为对比基线后，后续编辑与基线叠加；基线按原配置保存，支持清除和导出。
- 任务/client 筛选和 1/10/60 秒窗口从已有 trace 统计；平滑、对数轴、拖选放大只影响显示。
- 导出完整 trace、manifest、配置、当前统计和 SVG 图表。导出的 trace 对应最近一次成功生成的配置；过滤后的复用仅基于过滤后的请求历史。

工作台与 CLI 共用生成器，不在 JavaScript 中复制随机合成逻辑。详见[使用说明](docs/workbench.md)。


## 配置与语义

完整可运行示例：[config.example.json](examples/config.example.json)。所有字段、默认值及分布参数化见 [配置协议](docs/configuration.md)。

```json
{
  "version": 3,
  "duration": 60,
  "seed": 42,
  "block_size": 128,
  "traffic": {"session_rate": 0.5, "arrival": {"cv": 1}},
  "prefix_groups": [{"key": "system", "tokens": 512, "scope": "task"}],
  "tasks": [{
    "key": "chat",
    "weight": 1,
    "clients": {"count": 4},
    "prefix_groups": [{"key": "system"}],
    "session": {
      "requests": {"distribution": "discrete", "values": [2, 4, 8]},
      "initial_private_tokens": 128,
      "growth_multiplier": {"distribution": "lognormal", "mean": 1, "cv": 0.3},
      "external_tokens": {"distribution": "gamma", "mean": 100, "cv": 0.8},
      "output_tokens": 200,
      "gap": {"distribution": "lognormal", "mean": 10, "cv": 1}
    }
  }]
}
```

- `traffic.session_rate` 单位为 sessions/s，可以是常数或时间控制点曲线。最终 RPS 来自多轮展开，不是另一个需要拟合的目标。
- Task 和 client 的 `weight` 分层归一化，控制基础 session 组成；局部 burst 和 client `activity` 在分配后生效，不压低其他来源。
- 每个 client 有独立的 Gamma/Weibull 发起时钟，变化强度通过积分时钟处理。曲线按 `traffic.resolution` 网格及拐点划段，以中点值近似；减小分辨率可检查数值收敛。
- Session seed 来自全局 seed、task key、client key 和 client 内序号。`structure/growth/output/timing` 子流隔离，调整开始时间不改变对应 session 的相对轨迹。
- `requests` 表示 LLM 调用次数。轮间 `gap` 是两次请求的完整到达间隔，不再加服务耗时。已有 session 不受后续 burst 的轮间压缩影响。
- 输入 token 数逐轮增加：上轮输入 + 上轮输出 + 本轮外部新增。增长倍率每个 session 采样一次，只作用于外部新增。先累计 token 再取完整 block，跨轮保留部分尾块。
- 公共前缀按 global/task/client 共享；私有后缀按 session 隔离。相同长度不意味着相同身份。完整 block hash 依赖此前前缀。

## 输出

`generate.py` 写入 trace 和 `<trace>.manifest.json`，支持 `.jsonl.gz`。同一配置、Python 随机实现及生成器版本下，普通 JSONL 和 gzip 均可重复生成相同字节。Manifest 包含配置快照、各 client 的有效强度、session seed/身份、计划与已输出请求数、截断信息及统计。

请求默认包含：

```json
{"timestamp":2.0,"request_index":0,"input_tokens":640,"output_tokens":200,"external_tokens":128,"session_id":"chat:client-000000:00000000","hash_ids":["...完整 block 身份..."]}
```

`output.request_metadata: false` 可只保留 `timestamp/hash_ids/session_id`。短请求的 `hash_ids: []` 仍输出；不删请求来满足比例。首请求的 `external_tokens` 是初始私有长度，后续是应用增长倍率后的外部新增。

时间窗口为 `[0, duration)`。Session 从零时刻的空系统开始发起，截止后的请求省略。活跃 session 从首请求持续到计划末请求，截止时截断；单请求或全零间隔 session 的活跃时间为零。它不等于执行中的推理并发数。

`run_synthetic.py` 额外写入 `config.json`、`report.json` 和可选 `curves.png/svg`。报告包括任务/client RPS、IAT、轮数、时长、长度、条件上下文分位数、历史前缀复用、复用间隔、token/s、自相关及间隔—增长二维统计。PNG/SVG 展示其中 12 组核心图。历史复用比例是无驱逐条件下的机会，不是实际缓存命中率。

## 参考配置与实现边界

[预设说明](examples/presets/README.md)包含 chat、coding agent、短流程问答、客服、串行研究、数据分析、长 agent、头部 client、日周期与 burst 混合配置。

当前实现配置驱动核心、CLI、JSON 统计、静态曲线与交互编辑/对比工作台。压缩、分叉、重试、human turn 层级、并发准入和 serving 反馈也不在基础核心中。详见[重构设计](docs/config-driven-refactor.md)。

仅接受 `version: 3` 的 task 配置。数据集采样、自动校准、旧准入/扰动实现及其转换前端、实验脚本和示例已删除；不再维护第二套生成路径。

## 验证

生成质量的实测对照见[论文规律验证](docs/paper-pattern-validation.md)：8 组人工配置、3 个 seed，对照 ServeGen/FineServe 的同口径统计，保留误差、截断影响及尚未表达的联合分布。可独立运行：

```bash
python3 experiments/validate_paper_patterns.py
python3 experiments/plot_paper_patterns.py
```

```bash
python3 -m unittest discover -s tests -q
npm --prefix web run build
npm --prefix web test
```

测试覆盖随机复现与隔离、流量分配、峰谷残余时钟、时间归并与截止边界、token 核算、部分尾块、共享范围、私有隔离和统计窗口独立性。通过这些检查不代表符合真实生产负载。
