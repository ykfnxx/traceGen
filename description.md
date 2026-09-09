# 数据合成仓库目标

我们现在需要开发一个数据合成的仓库，用于合成llm serving trace，整体使用serveGen的思路合成请求的时序分布

## 要求

### 超参

block_size: 控制block粒度，按照block粒度来生成
duration: 合成数据集的持续周期
load_scale: 整体请求时序强度倍率，默认1；同时缩放session间和session内的时间间隔
base_session_rate: session起始的基准到达率，单位session/s
seed: 随机种子，用于复现实验

### 输入

可配置的多种参考数据集，类似weka、tracelab等，用于session内的负载规律分布和不同任务类型的负载分布合成

### 输出

以request为单位逐行组织数据集，每个request包含几个部分：timestamp, hash_ids, session_id

hash_id的数量需要由block_size决定

hash_ids表示请求上下文中完整KV cache block的前缀hash，数量为floor(token_count / block_size)，不足一个block的尾部不加入。每个hash必须体现此前整个前缀，不关注具体文本内容。

第一版按数据源/任务权重抽样完整session模板，保留模板的请求数量、内部相对时序和前缀关系；使用Gamma/Weibull分布生成session起始时间，再通过load_scale统一缩放时间轴。不同session的负载差异来自参考数据，不额外设计复用分布。

输出仅保留[0, duration)内的请求，截止处省略的后续请求数量记录在manifest中。仅有源block hash时，目标block_size必须是源block_size的正整数倍。
