# 数据合成仓库目标

我们现在需要开发一个数据合成的仓库，用于合成llm serving trace，整体使用serveGen的思路合成请求的时序分布

## 要求

### 超参

block_size: 控制block粒度，按照block粒度来生成
duration: 合成数据集的持续周期
max_concurrent_sessions: 同时活跃session数量的硬上限
session_rate: 新session候选到达率，单位session/s，用于调节并发压力
arrival: 候选session到达间隔分布；Gamma的cv调节随机突发程度
bursts: 指定时间窗口覆盖session_rate，以控制burst流量
seed: 随机种子，用于复现实验

### 输入

可配置的多种参考数据集，类似weka、tracelab等，用于session内的负载规律分布和不同任务类型的负载分布合成

### 输出

以request为单位逐行组织数据集，每个request包含几个部分：timestamp, hash_ids, session_id

hash_id的数量需要由block_size决定

hash_ids表示请求上下文中完整KV cache block的前缀hash，数量为floor(token_count / block_size)，不足一个block的尾部不加入。每个hash必须体现此前整个前缀，不关注具体文本内容。

按数据源/任务权重抽样完整session模板，保留请求数量、内部相对时序和前缀关系。使用Gamma/Weibull分布与分时段速率生成候选session；达到并发上限时按FIFO延后新session开始，已启动session内部请求时序不变。不同session的负载差异来自参考数据，不额外设计复用分布。

只关心到达的请求，默认都能处理，不模拟请求执行或完成。session在第一条请求到达时开始活跃，最后一条请求（含subagent）到达后释放。旧load_scale整体时间缩放强度定义废弃，不再使用。

输出仅保留[0, duration)内的请求，截止处省略的后续请求数量记录在manifest中。仅有源block hash时，目标block_size必须是源block_size的正整数倍。
