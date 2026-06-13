# Echo 全面审计报告

> 本报告逐条核对 proposal（[proposal.tex](proposal.tex)）里承诺的每一个特性与代码实现的差距，并记录一次对 Dashboard UI 的逐 widget / 逐交互审计。状态分级：✅ 已实现 · ⚠️ 部分/有偏离 · ❌ 未实现 · 🔧 本次已修。
>
> 审计日期：当前会话。审计范围：`plugins/echo_signals/` 全部模块 + dashboard 前后端 + 评估 harness + 皮肤。

---

## 更新 · 本轮已闭环（审计后立即实现）

审计列出的 backlog 已实现 6 项（每项独立 commit + 测试 + 推送）。下方原始审计正文保留作记录，但这些项现已闭环：

| 原审计缺口 | 状态 | commit |
|---|---|---|
| **UI**：ThumbsBar hooks 崩溃 / 打分无 timeline / drift 无 timeline / drift badge | 🔧 全修 + 回归测试 | 253e06e54 |
| **Layer C**：judge 只调一次 → PRM 多次投票（3 票严格多数降噪） | ✅ 实现 | 27c0551ab |
| **M4**：初始置信度永远 0.5 → save-intent 创建的技能 0.65 | ✅ 实现 | 7010f7d43 |
| **Layer A**：无 exit code 信号 → tool_error 信号 + 第 3 个 drift metric | ✅ 实现 | 1d70a8d5f |
| **exclusion 死数据**：写而不读 → 经 M5 注入通道下发给 agent（cache-safe） | ✅ 生效 | 7f374c63c |
| **M4**：手动编辑锁定 set_locked 零调用 → SKILL.md hash 检测 + 归因（区分用户/Hermes 编辑） | ✅ 实现 | 985df62b4 |
| **评估**：缺 Metric 2 → 错误传播率（Echo 抓 3/3 坏技能，Baseline B 抓 0/3） | ✅ 实现 | a41e4f3a5 |

**仍未做（需要外部资源或更大设计决策，留给你）**：M1 编辑距离信号（CLI/TUI 难捕获初稿 vs 终稿）、M2 选 B 真拆方法论/具体两层（需 LLM 拆分技能文本）、task_type_tag 语义标签（需 taxonomy）、scope_level 影响 M5 检索广度（需定策略）、半合成数据（Enron/CodeAlpaca）、真实 Telegram bot、Metric 1 满意度曲线 / Metric 3 token 开销、统计检验。

下方为审计当时的原始记录 ↓

---

## 一、总评

代码工程质量高、测试覆盖好（409 Echo 单测 + 21 评估 + 78 Hermes 回归 + 34/34 smoke 全绿），五大模块的**骨架都在**。但对照 proposal 逐条看，有若干"画了但没完全落地"的饼，分两类：

1. **被有意简化/代理的**（已在 CLAUDE.md 文档化）：M1 编辑距离→轮数计数、语义复现用哈希 embedding、drift 用全历史 Welford 而非滑窗。
2. **schema 留了位但代码没填的**（容易被误以为做了）：M2 技能拆分两层、手动编辑锁定、task_type_tag、初始置信度按上下文。

UI 方面本次审计发现 **1 个会崩溃的严重 bug + 3 个"功能看起来在但实际无效"的 bug**，已全部修复并加回归测试。

---

## 二、UI 审计（你最关心的部分）

### 🔧 已修复的 bug（commit 253e06e54）

| # | 级别 | 现象 | 根因 | 修复 |
|---|---|---|---|---|
| 1 | **CRITICAL** | 新建技能后 chat 底部的大拇指**整个消失/崩溃** | `ThumbsBar` 的 `submit` useCallback 写在 `if (pendingScope) return` 之后，scope 问题出现时 hook 数从 10 掉到 9，React 抛 "rendered fewer hooks" 卸载整个 widget。**这正是新建技能场景** | 把所有 hook 提到条件返回之前 |
| 2 | HIGH | 点了大拇指，置信度变了，但 timeline 里**看不到这次打分**，"Signals" 计数也不涨 | `/feedback` 只更新 `echo_skill_confidence`，从不写 `echo_signal_event`，`SIGNAL_BADGES` 里的 `explicit_positive/negative` 徽章是死的 | `/feedback` 现在写 Layer B signal_event（attribute 到最近 invocation）+ bump n_signals |
| 3 | HIGH | 技能因 drift 掉置信度，timeline 里**看不到"为什么掉"** | `baseline.finalize_invocation` 改置信度但不写 signal_event | drift 时写 Layer A `drift_detected` 事件（z-score 进 value_real，metric 进 value_text） |
| 4 | MINOR | drift 事件即便写了也没有徽章 | `SIGNAL_BADGES` 缺 `drift_detected` | 加橙色 "⚠ drift detected" 徽章 |

**"打分到底有没有用"的结论**：修复前——置信度会动，但审计轨迹（timeline + 信号计数）完全看不到，且新建技能时大拇指会崩溃消失。修复后——打分写入完整轨迹、可在 timeline 逐条回看、计数正确。

### ⚠️ 已知设计局限（未修，需你定夺）

| 项 | 说明 | 影响 | 建议 |
|---|---|---|---|
| **大拇指可能误归因** | ThumbsBar 用 `/invocations/recent?limit=1` 当"当前技能"，取的是全局最近一次 invocation。若你在一个还没加载任何技能的新会话里点赞，会算到**上个会话的旧技能**头上 | 中 | chat 是 PTY，Echo 看不到当前轮用了哪个技能。要根治需更深集成；或在 UI 上显示"当前归因技能名 + 时间"让用户能察觉错配（现在其实已显示 skill_id，可加一个"X 分钟前"时间戳） |
| **长按竞态** | `makePressHandlers` 每次渲染重建闭包，5s 轮询若恰好在长按途中触发重渲染，旧计时器与新 handler 状态会错位 | 低 | 概率极小（按压 <0.5s，轮询 5s）。可用 useRef 固化计时器 |
| **dashboard 无处展示 scope/exclusion/methodology** | 这些字段在 UI 上完全不可见 | 低 | 那些功能本身也没 populate（见下），等功能补齐再加展示 |
| **dashboard 配色是通用 zinc** | 本次已加 teal 页头点缀（◉ Echo + 青绿描边），但卡片内部仍是 Hermes SDK 的中性色 | 低 | 卡片用的是 Hermes 的 `C.Card` 组件，不宜强改；页头点缀已足够建立 Echo 身份 |

### UI 逐 widget 核对（全部正常）

| Widget | API | 状态 |
|---|---|---|
| SkillRanking | `GET /skills` | ✅ 含 🔒 锁定徽章（但锁定永不触发，见 M4） |
| StatusDistribution | `GET /status-distribution` | ✅ |
| RecentInvocations | `GET /invocations/recent` | ✅ |
| SkillTimeline | `GET /skills/{id}/timeline` | ✅ 修复后徽章完整 |
| CandidateQueue (M1) | `GET /candidates` | ✅ 空态文案与阈值一致 |
| PreferenceLibrary (M5) | `GET/DELETE /preferences` | ✅ 展开/删除幂等 |
| StatusStrip | `GET /status` | ✅ |
| ScopeQuestion | `GET /scope/pending` + `POST /scope` | ✅ hook 顺序正确 |
| ThumbsBar | `POST /feedback` | 🔧 已修 hook 崩溃 |

---

## 三、Proposal 逐条核对（"画的饼"清单）

### Module 1 — 自适应创建触发（4 并行条件）

| 条件 | proposal 原文 | 实现 | 状态 |
|---|---|---|---|
| 1 显式请求 | "把这个流程存下来" | `detect_save_intent` 中英正则，权重 100 | ✅ |
| 2 语义复现 | "embedding 余弦相似度，过去 N 天" | 默认**哈希 embedding（词法代理）**，配 `ECHO_EMBEDDING_PROVIDER=openai` 才是神经 embedding | ⚠️ 默认非神经，已文档化 |
| 3 编辑精力 | "交互轮数多 **且最终输出与初稿编辑距离大**" | 只有 `user_turns≥3` 计数，**完全没有编辑距离** | ❌ 偏离 proposal 措辞 |
| 4 过程复杂 | "≥5 工具调用" | `tool_calls≥5`，权重 30 | ✅ |
| 触发动作 | "任意满足即触发技能创建流程" | Echo 是 **nominator**（放进 CandidateQueue），不自动建技能 | ⚠️ 语义偏离：实际由用户/curator 决定 |

### Module 2 — 适用范围确认

| 项 | proposal | 实现 | 状态 |
|---|---|---|---|
| A/B 二选一 | ✓ | ScopeQuestion + `/scope` (broad/narrow) | ✅ |
| 选 B 拆两层 | "拆分为方法论层 + 场景绑定具体层" | schema 有 `methodology_layer`/`specifics_layer` 列，**`set_scope` 从不写**，只存 scope_level 字符串 | ❌ 未实现（schema 留位但代码空） |
| 创建前确认 | "Echo 不会立即存储，先问" | Hermes 无 `pre_skill_create` hook，Echo 只能在 `post_tool_call` **事后标注**，技能其实已写盘 | ⚠️ 语义偏离：事后标注非创建前拦截 |
| **scope_level 影响复用广度** | "确定技能未来应被多广泛复用" | **`scope_level` 写了但全代码无人读取**——M5 检索/技能加载都不消费它。答 A/B 只是存了个字符串，不改变任何行为 | ❌ **写而不读的死数据** |

> ⚠️ **关键发现：M2 与 Layer C 的输出都是"只写不读"。** `scope_level`（broad/narrow）和 `exclusion_conditions`（judge 的"该技能在此场景不适用"输出）都被写入 `echo_skill_scope`，但**没有任何下游逻辑读取它们来改变技能的加载或复用**。根因是 Echo 作为非侵入插件无法拦截 Hermes 的技能检索路径（那需要改核心文件）。所以：用户答 scope 问题、judge 产出排除条件，数据都真实落库了，但当前对 agent 的实际行为零影响。这是 proposal "适用边界" 与 "排除条件" 两个卖点的**根本性落地缺口**——不是 bug，是架构约束，但报告不应宣称这些机制"生效"。

### Module 3 Layer A — 行为信号

| 信号 | proposal | 实现 | 状态 |
|---|---|---|---|
| 是否被复制 | ✓ | 仅 Tauri 桌面壳 `clipboard_copy`，CLI/TUI/web 无 | ⚠️ 仅桌面壳 |
| 复制后编辑距离 | ✓ | 剪贴板只存长度+200字符，**无编辑距离** | ❌ 未实现 |
| 修改轮数 | ✓ | `user_turn` → `modification_round_count` | ✅ |
| 换说法重提 (rephrase) | ✓ | 等同 M1 `m1_semantic_recurrence`，只喂 M1 提名，**不作为独立 confidence drift 信号** | ⚠️ 部分 |
| 会话立刻结束 | ✓ | `session_ended` 信号 | ✅ |
| 工具 exit code | ✓ | `post_tool_call` 只存 tool_name，成功/失败解析 deferred | ❌ 未实现 |
| 基线+分布偏移 | "当前滑动窗口的行为分布" | Welford **全历史**均值/方差（非滑窗），N_WARM=20，z≥2 触发 | ⚠️ 全历史而非滑窗 |
| 追踪 metric | 上述全部 | 实际只有 2 个：`modification_round_count`、`tool_call_count` | ⚠️ 只 2 个 |

### Module 3 Layer B — 语言信号

| 项 | 实现 | 状态 |
|---|---|---|
| 显式评分 thumbs | ✅ 修复后写完整轨迹 | ✅ |
| NL 情感分类 | `nl_classifier` task=echo_classifier，保守偏 neutral | ✅ |
| 是否调 LLM | 取决于 `echo.aux_mode`（Step 20），默认 shared | ✅ 可配 |

### Module 3 Layer C — 按需 judge

| 项 | proposal | 实现 | 状态 |
|---|---|---|---|
| 仅 drift 触发 | ✓ | active→pending_review 时启动 | ✅ |
| 不同系列模型 | "避免同源偏差" | 现在可配 separate（Step 20），**默认 shared = 同模型** | ⚠️ 默认未消除同源偏差 |
| **PRM 多次投票** | "参考 OpenClaw-RL PRM 多次投票降噪" | judge **只调一次 LLM** | ❌ 未实现 |
| 输出排除条件 | "追加到排除条件列表" | verdict=exclusion → `exclusion_conditions` JSON 追加 | ✅ |

### Module 4 — 置信度引擎

| 规则 | proposal | 实现 | 状态 |
|---|---|---|---|
| c∈[0,1] | ✓ | ✅ | ✅ |
| 初始值按上下文 | "显式请求的技能初始置信度较高" | **永远 0.5**（schema 默认 + INSERT OR IGNORE） | ❌ 未实现 |
| thumbs +0.1 / NL +0.05 / drift ×0.85 / neg ×0.7 / silence 不变 | ✓ | ✅ 全部正确（且公式已与报告逐字符对齐） | ✅ |
| **手动编辑→锁定** | "标记锁定，不允许覆盖" | `set_locked` 函数存在但**零调用**，无文件系统监听 | ❌ 未实现（locked 列 + 🔒 徽章是死的） |
| c<0.3 待验证 / c<0.1 淘汰 | ✓ | ✅ | ✅ |

### Module 5 — 偏好库

| 项 | proposal | 实现 | 状态 |
|---|---|---|---|
| 只存 ≥4分 | ✓ | rating 4/5 | ✅ |
| 含 embedding/评分/**任务类型标签**/时间戳 | ✓ | `task_type_tag` 列存在但**从不写**（永远 NULL），其余有 | ⚠️ 标签缺 |
| top-k + few-shot 注入 | ✓ | ✅ | ✅ |
| MMR | ✓ | ✅ | ✅ |
| 容量上限按 评分×时间×次数 淘汰 | ✓ | composite_score LRU | ✅ |

### 评估计划（§4）

| 项 | proposal | 实现 | 状态 |
|---|---|---|---|
| 三层数据 | 合成/半合成(Enron,CodeAlpaca)/真实(Telegram) | **只有合成**（Step 19 harness） | ⚠️ 仅合成 |
| 三对照组 | A 纯agent / B 自评估+频率衰减 / Echo | harness 有 plain + signals-only ablation，**没有 Baseline B（自评估+频率衰减）** | ⚠️ 缺 B |
| Metric 1 满意度曲线 | 时间轴 1-5 分均值 | M4 校准（Spearman ρ）是代理，**非时间轴满意度曲线** | ⚠️ 代理 |
| Metric 2 错误传播率 | 注入 10 个坏技能追踪存活 | **无此场景** | ❌ 未实现 |
| Metric 3 系统开销 | token<15%，延迟 | **未测量** | ❌ 未实现 |
| 统计检验 | paired t-test / Mann-Kendall | **未实现** | ❌ |

### Challenges（§5）

| 项 | 状态 |
|---|---|
| C1 冷启动 | ✅ N_WARM=20（但初始置信度固定 0.5，未按上下文） |
| C2 NL 分类边界 | ✅ 保守偏 neutral |
| C3 scope UX + 不回应默认策略 | ⚠️ A/B 实现，但"用户不回应"无超时默认（一直 pending） |
| C4 不修改权重天花板 | ✅ 设计取舍 |

---

## 四、按优先级排序的待办（睡醒后定夺）

### 高价值、低成本（建议优先做）
1. **初始置信度按上下文**（M4）：save-intent 创建的技能给更高初始 c（如 0.6）。改 `scope_dialog`/`usage_hook` 的 anchor insert，~10 行。
2. **task_type_tag 写入**（M5）：从用户消息/skill 名派生一个粗标签存进去。~15 行。
3. **大拇指归因可察觉**：ThumbsBar 显示"归因技能 + 距今时间"，让用户能发现错配。纯 UI。

### 中等成本（proposal 核心卖点，值得补）
4. **Layer C 多次投票**（M3）：judge 调 N 次取多数，proposal 明确承诺、且是"降噪"核心。~30 行 + 测试。
5. **工具 exit code 信号**（Layer A）：解析 `post_tool_call` 的成功/失败，新增 drift metric。
6. **M2 技能拆分两层**：选 B 时真正写 `methodology_layer`/`specifics_layer`（需要一次 LLM 调用拆分技能文本）。

### 大工程（评估实验，按需）
7. Baseline B（自评估+频率衰减）对照组；Metric 2 错误传播率场景（注入坏技能）；Metric 3 token/延迟测量。
8. 编辑距离信号（M1 条件3 + Layer A）：需要捕获"初稿 vs 终稿"，CLI/TUI 难拿到。
9. 手动编辑锁定：需要文件系统监听 SKILL.md 的 mtime/hash。
10. 半合成数据（Enron/CodeAlpaca）+ 真实 Telegram bot。

### 文档诚实性建议
- 报告里 Layer C "independent auditor breaks same-source bias" 应加限定："when configured with a separate aux provider"（默认 shared 时同源偏差未消除）。
- M1/M3 涉及"编辑距离"的措辞，报告里应说明用轮数代理。
- "PRM 多次投票"若不补实现，报告不应宣称已做。
