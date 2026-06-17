<p align="center">
  <img src="DevPlan/Echo.png" alt="Echo" width="320">
</p>

<h1 align="center">Echo</h1>

<p align="center">
  <b>一个会自我改进的 Agent:它通过你对技能的反应,判断哪些技能是真的好用。</b>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange?style=for-the-badge" alt="Status: alpha">
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/forked%20from-Hermes%20v0.14.0-blueviolet?style=for-the-badge" alt="Forked from Hermes v0.14.0">
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-lightgrey?style=for-the-badge" alt="English"></a>
</p>

> 🌐 **English version: [README.md](README.md).**

---

## Echo 是什么?

Echo 从 [Hermes Agent](https://github.com/NousResearch/hermes-agent) v0.14.0 分叉而来。
Hermes 本身就会学习:从经验里写技能、复用技能、跨会话保留记忆。问题是这个闭环是自己给自己打分——
写技能的模型,也是判断这技能好不好用的模型;技能只在 agent 调用工具满 5 次时才创建;而且技能一旦
存在,就在所有场景被推荐。Echo 的做法是:不再信任 agent 对自己工作的评价,改用用户的真实反应。

| Hermes 的问题 | Echo 的做法 |
|---|---|
| 写技能的模型,同时也是给它打分的模型。 | 换一个独立的模型来审计,再加上完全不用 LLM 的行为漂移检测。两者都从用户侧打分,不是从 agent 侧。 |
| 技能只靠写死的"工具调用满 5 次"规则来创建。 | Echo 还会看保存意图的措辞、你改了多少输出、请求是否重复;而且创建前会先问,而不是悄悄生成。 |
| 技能一旦生成,就在所有场景被推荐。 | Echo 会问技能该用在哪;审计员可以把它从表现差的场景里排除掉。 |

这东西最初是个 Hermes 插件,现在不是了——要把上面这些做出来,得改 Hermes 的核心,光靠钩子不够。
Echo 现在是一个独立的 Agent,固定在 Hermes v0.14.0,不再跟上游版本。它所依赖的 Hermes 底座
(多平台网关、终端后端、MCP、定时调度,以及它扩展的技能与记忆系统)都还在。

## 工作原理

Echo 钩住 agent 主循环,把自己的 `echo_*` 表写进 agent 已有的那个 SQLite 库。三层信号驱动五个模块。

**信号层**
- **Layer A —— 行为信号,不用 LLM。** 对每个技能在线维护基线(Welford 均值/方差),覆盖修改轮次、
  工具调用次数、工具报错;用 z-score 识别漂移。
- **Layer B —— 情感。** 每个用户回合由辅助模型分类为正/负/中性,拿不准时偏向中性。
- **Layer C —— 按需审计。** 某技能被标记后,换一个独立模型投票判断它是没问题、降级、还是该从某场景排除。

**模块**
- **M1 —— 技能创建触发。** 保存意图、复杂度、修改投入、重复性;创建前先问。
- **M2 —— 范围确认。** 用 `clarify` 在对话里问技能该用在哪。
- **M3 —— 审计与排除。** Layer C 审计员,加上把排除条件注回给 agent。
- **M4 —— 置信度引擎。** 一个衰减状态机:`active → pending_review → retired`。
- **M5 —— 偏好 RAG。** 一份合并式偏好画像,加上按技能置信度加权的示例检索。

## 入口形态

Echo 跑在和 Hermes 一样的几种界面上,外加一个原生 App:

- **CLI / TUI** —— 带青色 Echo 皮肤的对话;信号在后台采集。
- **Web 仪表盘** —— 一个 `/echo` 页面:置信度排名、状态分布、技能候选队列、偏好库,以及对话内评分组件。
- **原生 macOS App**([`desktop/Echo/`](desktop/Echo/))—— SwiftUI 前端,以子进程方式拉起网关,
  并采集浏览器拿不到的信号(剪贴板、窗口焦点)。

## 快速上手

所有东西都走仓库根目录的一个启动器:

```bash
./echo chat      # CLI 对话 —— 信号后台采集
./echo tui       # 全屏 TUI
./echo dash      # Web 仪表盘(浏览器打开 /echo)
./echo app       # 原生 macOS App
./echo verify    # 跑测试套件 + 端到端冒烟检查
./echo --help    # 其余形态
```

模型、供应商、API 密钥的配置方式和 Hermes 一样 —— 见 [Hermes 文档](https://hermes-agent.nousresearch.com/docs/)。
Echo 只多加了一步设置,用于配置可选的审计员模型。

## 仓库结构

代码树的大部分是 Echo 所基于的 Hermes 代码。Echo 自己的代码主要在这几处:

| 路径 | 内容 |
|---|---|
| [`plugins/echo_signals/`](plugins/echo_signals/) | Echo 的主体 —— schema、钩子、信号采集、五个模块 |
| [`tests/plugins/echo_signals/`](tests/plugins/echo_signals/) | 单元测试 |
| [`desktop/Echo/`](desktop/Echo/) | 原生 macOS App |
| [`scripts/eval/`](scripts/eval/) | 评测框架与指标脚本 |
| [`DevPlan/`](DevPlan/) | 提案、schema 规范、设计文档、实验报告 |
| [`docs/hermes-architecture.html`](docs/hermes-architecture.html) | 读 Hermes 内部实现的辅助文档 |

Echo 也在几个地方直接改了 Hermes(网关、Web 和 TUI 前端),所以这是个 fork —— 它没法和上游干净地
diff,也不打算合并回去。其余部分都是 Hermes,以其 MIT 许可证一并包含,使本项目能独立运行。

## 评测

为了不让 agent 自己给自己打分,评测用了四个相互独立的模型——一个当模拟用户,一个当独立评分者,
一个当被测 agent,还有 Echo 自己的信号模型。评测对照两个公开偏好基准
([PersonaMem](https://huggingface.co/datasets/bowen-upenn/PersonaMem) 和
[PrefEval](https://huggingface.co/datasets/siyanzhao/prefeval_explicit)),以及一个模拟用户闭环。
完整方法和数据——包括最初设定的开销目标在哪没达成——见
[`DevPlan/experiment-report-zh.md`](DevPlan/experiment-report-zh.md)(中文)和
[`experiment-report.md`](DevPlan/experiment-report.md)(English)。

## 致谢与许可证

Echo 从 [Nous Research](https://nousresearch.com) 的
**[Hermes Agent](https://github.com/NousResearch/hermes-agent)** 分叉而来。整个底座——网关、
终端后端、MCP、定时调度,以及 Echo 所基于的技能/记忆系统——都来自那里。

Echo 由西湖大学 Lingchao Nie、Fanghui Xu、Yuing Zhou 开发。

采用 MIT 许可证 —— 见 [LICENSE](LICENSE)。版权所有 © 2025 Nous Research;
修改及衍生作品 © 2026 Echo 作者。
