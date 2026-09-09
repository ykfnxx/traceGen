# Weka 试验合成报告（2026-09-09）

实现与试验均在本地完成，Python 3.14.4。原始数据按只读方式使用。

## 参考数据

源文件：`/home/solidyang/workspace/G35/datasets/cc-traces-weka-061326/traces.jsonl`。

源 SHA256：`5c4190caf696f8e5915c9c99bc1f698010d6c08c0225095b7da813f6210a0b50`。

独立扫描确认 183 个 session、26,648 个主请求、18,342 个 subagent 请求，共 44,990 个模型请求。session 请求数中位数 75，范围 20–4,095；session 持续时间中位数 7,390.795 秒（约 2.05 小时），最长约 90.56 小时。

## 配置与结果

`block_size=128`，`duration=3600` 秒，`base_session_rate=0.01` session/s，Gamma CV=1.5，seed=42。每份数据都从无活跃 session 的时刻开始。

| load_scale | session 数 | 不同模板数 | 请求数 | block 数 | 实际 RPS | 截断 session 数 | 截止后省略请求 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 10 | 10 | 346 | 203,775 | 0.096111 | 10 | 3,250 |
| 1 | 28 | 27 | 898 | 587,174 | 0.249444 | 24 | 7,922 |
| 2 | 62 | 53 | 3,623 | 3,047,292 | 1.006389 | 48 | 9,683 |

`load_scale` 严格表示整体时间轴缩放。此处不是稳态 RPS 实验：Weka 会话较长，起始阶段与截止截断影响显著，所以实际请求数并不严格随倍率线性增加。

## 验证证据

- 18 项标准库 unittest 全部通过，含固定种子的版本化 golden、前缀分叉、尾块处理、嵌套时序、Gamma/Weibull 统计、混合权重及错误输入保护。
- 三份输出共 4,867 个请求、3,838,241 个 block，全部通过独立原始 Weka 读取逻辑的验证。
- 逐请求检查输出字段、uint64 hash、全局时间排序、时间窗口、完整 block 数和 session 内相对时间。
- 4,767 对相邻 session 请求的目标 block 公共前缀长度与源数据一致。
- 逐 session 核对窗口内没有遗漏请求；截断数量与 manifest 一致。
- 0.5× 的全部 346 个请求与 1× 的对应前缀逐行匹配；1× 的全部 898 个请求与 2× 对应前缀逐行匹配。session ID 与 hash 不变，时间准确缩放。
- `python3 -m compileall -q tracegen generate.py experiments tests` 通过。

## 产物与复现

输出目录：`runs/weka/`。每份 JSONL 都有 `.manifest.json`，包含源 session 映射和源数据指纹。`report.json` 记录统计、每 300 秒请求数、校验结果和试验时的代码 SHA256。大文件未纳入 Git。

```bash
python3 -m unittest discover -s tests -v
python3 experiments/run_weka.py \
  --source /home/solidyang/workspace/G35/datasets/cc-traces-weka-061326/traces.jsonl \
  --output-dir runs/weka
```

| 文件 | 未压缩内容 SHA256 |
|---|---|
| `weka_scale_0.5.jsonl` | `5495d37bc22b532d433f2101b9e4c58a78ae39bfbc5c62747ae6e245191d0734` |
| `weka_scale_1.jsonl` | `61d57e813f115d652dba590ad0192a4304606e641a80d0077b9ede35eaab92d2` |
| `weka_scale_2.jsonl` | `ed994f4fbd058f9441cf2772d9074258c5dac514c1429fa8760d94c18a71fac1` |

本次真实数据验证覆盖 Weka；多来源混合使用人工 chat/agent 示例验证。TraceLab 可转换为通用 session JSONL 输入，尚无真实 TraceLab 专用适配试验。

Weka 使用 64-token hash block 的输入长度代理；128 粒度按两块合并。未生成原始文本，也未验证与实际 LLM serving 部署的 hash ABI 一致。
