# 交互工作台

## 启动

```bash
npm --prefix web ci
npm --prefix web run build
python3 preview.py --port 8765 --output-dir runs/workbench
```

浏览器访问 `http://127.0.0.1:8765`。服务绑定回环地址；生成与导出文件位于 output-dir，以完整配置摘要分目录保存。重新启动进程后重新生成即可恢复运行；可先导出配置作为持久化方案。修改源文件后需要重新 build。

开发时先启动 `python3 preview.py`，再运行 `npm --prefix web run dev`，Vite 把 `/api` 转发到 Python 服务。前端是 React + Vite，图表为原生 SVG；没有第二份随机生成实现。

## 编辑配置

- **整体**：duration、seed、block_size、整体 session 强度曲线、插值、周期、相位、积分分辨率、默认发起分布、全局 burst 和请求 metadata。
- **任务**：添加/删除 task，修改稳定 key、权重曲线、client 数量/头部集中度；切换显式 client 后可单独编辑 key、权重、activity、发起分布及局部 burst。Task 也有默认时钟和局部 burst。
- **Session 分布**：请求数、初始私有长度、增长倍率、外部新增、输出长度及到达间隔；固定、Gamma、Lognormal、离散类型，数值参数或离散 values/weights。展开控件查看 4096 次独立抽样的 CDF 和抽样 PDF/概率质量，属于抽样预览而非解析 PDF。
- **前缀**：添加/删除组，修改 key、长度、global/task/client scope；任务面板选择组并设置热度权重。组改名同步更新任务引用，删除组同步移除引用。
- **JSON**：编辑全部配置，经过同一 Python 配置校验后应用。非法 JSON 或字段显示错误，不替换上次成功生成的结果。

主图可拖动全局强度、任务权重、client 权重/活跃度控制点。曲线编辑器的“在主图编辑”切换目标；控制点也支持方向键微调。Burst 小图可拖动起止时间和倍率，ramp/加量用数值框编辑。拖动过程中仅更新输入，松开后生成；数值编辑在短暂防抖后自动生成，也可点击“生成预览”。

曲线中的权重不是请求占比。全局编辑曲线不含 burst；结果区的有效发起强度包含 burst、任务/client 分配和 activity。所有参数口径见[配置协议](configuration.md)。

## 查看结果与对比

结果区分别显示输入 sessions/s 与 serving requests/s。先选任务，再按需选该任务的 client；支持 1/10/60 秒统计窗口、移动平均窗口数、对数轴，并通过拖选 RPS 图或起止数值局部放大。平滑只用于时间序列，不改变 CDF、原始统计或 trace。

“固定为对比基线”保存最近成功生成的完整配置、seed 和运行 ID。后续修改重新生成当前结果，基线保持不变；两份结果使用相同来源和窗口统计。图中橙色虚线表示基线。可显式修改 seed，界面同时显示两个 seed，避免把随机变化误认为单一参数影响。基线曲线按 task/client key 匹配，不按数组位置配对。

三个结果页覆盖到达与组成、会话与增长、前缀与属性：任务堆叠、client RPS、首请求率、活跃 session、IAT/CDF、请求计数自相关、session 计划/观察规模、条件上下文分位数、长度分布、前缀比例、block 重访间隔、token/s、输入长度时间分位数和间隔—外部增长二维分箱。

筛选后指标和复用历史仅基于被选中的请求。初始公共前缀比例不等于历史可复用比例，两者都不等于有限容量缓存命中率。关闭 metadata 后长度相关图没有数据，不能从完整 block 数猜测实际 token 长度。

## 导出与重放

- “导出配置”保存当前编辑配置；“导出基线配置”保存冻结配置。
- “导出完整 Trace / Manifest”下载最近成功运行的完整产物，不应用显示筛选。
- “导出统计摘要”保存当前窗口/来源的原始统计及对比基线摘要，不把显示平滑值当作原始统计。
- “导出当前 SVG 图表”保存当前结果页的可缩放曲线，包括缩放与叠加状态。

可用 `python3 generate.py --config saved.json --output runs/replay.jsonl` 重放。工作台和 CLI 相同配置的输出应逐字节相同。输入错误或新配置生成中，图表与 trace 导出仍对应上次成功结果，状态栏会明确标注。

## 验证

```bash
python3 -m unittest discover -s tests -q
npm --prefix web run build
cd web
npx playwright install chromium
npm test
```

本地环境若为 Playwright 尚未识别的 Ubuntu 26.04，可用 `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 npx playwright install chromium` 安装兼容浏览器包。测试覆盖实际拖拽、松开后生成、基线不变、窗口/筛选不重抽样、导出一致、分布/前缀编辑、非法配置恢复和移动端溢出。截图采用 Chromium；构建通过本身不代替交互验证。
