# 数据合成仓库目标

我们现在需要开发一个数据合成的仓库，用于合成llm serving trace，整体使用serveGen的思路合成请求的时序分布

## 要求

### 超参

block_size: 控制block粒度，按照block粒度来生成
new_block_jitter: 新增block数的相对随机扰动幅度，范围[0,1]，默认0.3，0为原始长度
duration: 合成数据集的持续周期
max_concurrent_sessions: 同时活跃session数量的硬上限
session_rate: 新session候选到达率，单位session/s，用于调节并发压力
arrival: 候选session到达间隔分布；Gamma的cv调节随机突发程度
bursts: 指定时间窗口覆盖session_rate，以控制burst流量
seed: 随机种子，用于复现实验

### 输入

原始数据分别由前端插件处理为统一的最小 session JSONL，每个数据集独立存储，后端分别读取。内置 Weka、SwissAI serving trace、LMSYS 前端，外部插件实现 configure/convert 接口即可扩展。

每行一个 session，只含 requests；每条请求只含 timestamp 和 hash_ids：

```json
{"requests":[{"timestamp":0,"hash_ids":[10,11]},{"timestamp":3,"hash_ids":[10,11,12]}]}
```

不包含 token 数、token 序列、provenance、hash_namespace、hash_id_scope 等字段。前端按配置的目标 block_size 完成 block 划分、尾部剔除及前缀 hash 编码；后端不再重新划分 block。所有输入需使用同一目标粒度。转换参数与时间来源可单独记录在转换报告中。

合成前剔除空 hash_ids 请求和全空 session，保留有效请求之间的相对时间，首个有效请求归零。输出不得有空 hash_ids。

合成时先按 datasets[].weight 选择数据集，再从该数据集中均匀采样 session，不预先将多个数据集合成一个。配比是 session 采样概率，不是请求数或 block 数比例。需要最终请求比例时，对 session 权重做计数回放校准，计入并发准入及窗口截断，以实际输出比例满足配置容差为准，不额外删减有效请求。各来源可独立配置 new_block_jitter；默认隔离抽样实例，需要保留跨 session 源身份时在数据集配置中指定 global 并关闭扰动。

SwissAI 使用固定窗口时明确记录为请求窗口，不能当作真实会话；LMSYS 缺失的逐轮时间为合成时间。这些解释不进入逐 session 输入字段。

### 输出

以request为单位逐行组织数据集，每个request包含几个部分：timestamp, hash_ids, session_id

hash_id的数量需要由block_size决定

hash_ids表示请求上下文中完整KV cache block的前缀hash，数量为floor(token_count / block_size)，不足一个block的尾部不加入。每个hash必须体现此前整个前缀，不关注具体文本内容。

按数据源/任务权重抽样完整session模板，保留请求数量、内部相对时序和前缀分支关系。默认对每次请求首次出现的前缀段长度施加随机扰动；已生成的共享前缀保持一致，不同合成session独立采样。缩放时保留请求端点和分叉点，每段至少一个block，防止错误合并分支。输出覆盖扰动后的完整上下文，不再要求每轮长度等于源模板。仅local身份支持独立扰动，global身份需显式关闭扰动。

使用Gamma/Weibull分布与分时段速率生成候选session；达到并发上限时按FIFO延后新session开始，已启动session内部请求时序不变。不额外设计跨session复用分布。

只关心到达的请求，默认都能处理，不模拟请求执行或完成。session在第一条请求到达时开始活跃，最后一条请求（含subagent）到达后释放。旧load_scale整体时间缩放强度定义废弃，不再使用。

输出仅保留[0, duration)内的请求，截止处省略的后续请求数量记录在manifest中。仅有源block hash时，目标block_size必须是源block_size的正整数倍。
