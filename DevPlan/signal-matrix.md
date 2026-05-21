# Echo 信号 × 平台可观测性矩阵

> 用于决定 Echo Layer A / Layer B 在每个平台上实际能采集什么信号。
> 数据来源：对 [gateway/platforms/](../gateway/platforms/) 各 adapter 的实际能力调研。
> 任何写在 paper 里的信号都必须在这个矩阵里有 ✅ 或带明确限制说明的 ⚠️。

## 完整矩阵

| 信号 | CLI | TUI | Telegram | Discord | Slack | WhatsApp | Signal | Matrix | Email | SMS | Web | ACP |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1. 用户复制 agent 输出 | ✅ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 2. 编辑距离(agent输出↔用户下条消息) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 3. 同任务内修改轮数 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 4. 用户 rephrase 同一请求 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 5. 会话在 agent 输出后立即结束 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ | ✅ | ⚠️ | ❌ |
| 6. 工具执行 exit code | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 7. 显式 thumbs / reaction | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 8. 自然语言反馈(下一条消息) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 9. 响应延迟(用户回复前思考时间) | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ |
| 10. 用户编辑/删除自己消息 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ❌ | ❌ | ❌ |

图例：✅ 可靠观测  ⚠️ 部分可观测/有限制  ❌ 不可观测

## 信号分类与对 Layer A 设计的影响

### 真正"开箱即用"的信号（建议作为 Layer A 主力）

**#3 修改轮数 (`modification_round_count`)**
- 所有平台都能测。基于 [gateway/session.py](../gateway/session.py) 的 session 上下文，统计 `/reset` 或 `/new` 之前 agent 完成的连续 turn 数。
- **核心 metric**：高轮数 → 用户在反复修改 → 可能表示技能不准确。

**#6 工具执行 exit code (`tool_exit_code`)**
- 所有平台都能测。`subprocess.returncode` 在 [gateway/run.py](../gateway/run.py) 已经被捕获。
- **使用方式**：技能内每个工具调用的 exit code 聚合。

**#8 自然语言反馈 (`nl_feedback`)**
- 所有平台都能测，因为它就是用户的下一条消息。
- ⚠️ **注意**：这是 Layer B 的信号，不是 Layer A——它需要 LLM 分类器才能转成结构化信号。

### 平台条件性信号（条件分支处理）

**#7 显式 reaction (`explicit_rating`)**
- 仅 **Telegram / Discord / Slack / Matrix / BlueBubbles** 支持。
- 在不支持的平台上：要么提供 `/rate` 斜杠命令作为 fallback，要么直接 disable。
- 引用：[gateway/platforms/telegram.py:5394](../gateway/platforms/telegram.py#L5394)、[gateway/platforms/slack.py:2247](../gateway/platforms/slack.py#L2247)、[gateway/platforms/matrix.py:2167](../gateway/platforms/matrix.py#L2167)

**#9 响应延迟 (`reply_latency_seconds`)**
- 所有平台都能测时间差，但**严格说不是"思考时间"**——只是"两条消息间隔"，混入了网络延迟、用户在干别的事、手机没看到推送等。
- 限制：长延迟可能=用户不耐烦也可能=用户去吃饭了。**建议作为辅助信号，不单独触发置信度衰减**。

**#5 会话立即结束 (`session_ended_within_window`)**
- 只有 Email 和 SMS 是"一次一回"的可靠"结束"信号。
- 其他平台靠 timeout 启发式（N 分钟没新消息）。
- ⚠️ 对群聊不可靠。

### 应该从 Layer A 删除的信号

**#1 复制 (clipboard activity)**
- 只有 CLI 通过 `/copy` 命令 + OSC 52 能测。所有 IM 平台、Web、TUI 都**完全黑盒**。
- **从 Layer A 删掉**。如果要做，只能做"显式 /copy 命令使用率"，是用户主动行为不是被动信号。

**#2 编辑距离 (agent 输出 vs 用户下条消息)**
- 没有任何平台天然能测。本质上要求"用户复制 agent 的输出回来再粘贴改一改"，这种使用模式罕见。
- **从 Layer A 删掉**。

**#4 rephrase 检测**
- 平台不直接给。需要在 Python 里跑 embedding 比较。
- ⚠️ Cheatsheet 提醒：embedding 走 [agent/auxiliary_client.py](../agent/auxiliary_client.py)，比 chat 便宜得多，所以这个信号**可以做**，只是**不属于"零成本 Layer A"**，应该归到 Layer B（轻量 LLM 层）。

**#10 用户编辑/删除自己消息**
- Matrix 技术上可能支持，但 Hermes 当前的 adapter **没注册 message_edit 事件 handler**。要做的话要先给 Hermes 加 adapter 能力。优先级低。

## Layer A 的修订定义

基于上面的现实，**Layer A 实际能采集的纯行为信号清单**应该改为：

| 信号名 | 类型 | 全平台可用 | 备注 |
|---|---|---|---|
| `modification_round_count` | INTEGER | ✅ | 一个 session 内同技能调用连续 turn 数 |
| `tool_exit_code_distribution` | JSON | ✅ | 该技能调用期间所有工具的 exit code 列表 |
| `reply_latency_seconds` | REAL | ⚠️ | 用户回复前的等待秒数；置信度低 |
| `session_ended_after_invocation` | BOOLEAN | ⚠️ | timeout 启发式，定义为 ≥10 分钟无新消息 |
| `explicit_rating` | INTEGER | 平台相关 | thumbs ±1，仅 TG/DC/Slack/Matrix |

**Layer B 接管** rephrase 检测（embedding 比对）和 NL feedback 情感分类。

**M2 用户主动选项** 接管所有需要显式 UX 的信号（满意度评分、技能适用范围确认）。

## 实施建议

1. 在 [plugins/echo_signals/](../plugins/) 的 plugin manifest 里给每个 platform 维护一个 `supported_signals` 列表。
2. 注册 hook 时检查当前 platform 的能力，**只采集该平台真正支持的信号**——不要在 schema 里硬编码"所有信号都有值"。
3. 评估实验主跑 **CLI** 或 **Telegram**：CLI 信号最齐全，Telegram 有 reaction 又是真实异步场景。
