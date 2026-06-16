# Echo Desktop App —— 完整开发计划（详细版）

> 把 Echo 从「Hermes Dashboard 网页插件 + Tauri 壳」升级为一个 **原生 macOS 应用**，外观复刻 macOS 27 "Golden Gate" 新 Siri App、采用 Liquid Glass；后端 **复用现有 Echo Agent Harness**（Hermes core + `plugins/echo_signals`），前端换成原生 SwiftUI 客户端接入 gateway。
>
> UI 表层细节见 [siri-app-ui-plan.md](siri-app-ui-plan.md)。本文件是 **工程实施手册**：精确协议契约、Swift 数据模型、任务级 WBS（可勾选）、时序、错误恢复、测试、交付。
>
> 协议字段全部来自实读源码：`tui_gateway/server.py`、`tui_gateway/ws.py`、`ui-tui/src/gatewayTypes.ts`、`ui-tui/src/types.ts`、`ui-tui/src/app/createGatewayEventHandler.ts`、`plugins/echo_signals/dashboard/plugin_api.py`。**改协议前以这些源文件为准复核**。

---

## 目录
1. [目标与非目标](#1-目标与非目标)
2. [系统架构](#2-系统架构)
3. [协议契约（精确）](#3-协议契约精确)
4. [Swift 工程结构与分层](#4-swift-工程结构与分层)
5. [核心数据模型（Codable）](#5-核心数据模型codable)
6. [GatewayClient 设计](#6-gatewayclient-设计)
7. [EchoAPIClient 设计](#7-echoapiclient-设计)
8. [关键时序流](#8-关键时序流)
9. [任务级 WBS（可勾选）](#9-任务级-wbs可勾选)
10. [错误处理与生命周期](#10-错误处理与生命周期)
11. [测试策略（分层）](#11-测试策略分层)
12. [打包 / 分发 / 签名](#12-打包--分发--签名)
13. [与现有 Echo 工程衔接](#13-与现有-echo-工程衔接)
14. [开放决策点](#14-开放决策点动工前需维护者拍板)
15. [里程碑与退出标准](#15-里程碑与退出标准)

---

## 1. 目标与非目标

**目标**
1. 原生 macOS App，外观 ≈ 新 Siri App（Liquid Glass）。
2. 复用 Echo 后端，**零改 Hermes 四个核心文件**（`run_agent.py` / `cli.py` / `gateway/run.py` / `hermes_cli/main.py`）。后端改动上限：在 dashboard FastAPI 上挂一行 WebSocket 路由（若尚未挂）。
3. 完整呈现 Echo 差异化价值：M1 提名（clarify）、M2 scope、M3/M4 置信度、M5 偏好注入、信号收集——这些必须在原生 UI 里**可见可操作**，不只是好看的聊天壳。
4. 与 `./echo` 启动器、`tui_gateway`、dashboard 插件 **共存**（新 App 是又一个前端 surface）。

**非目标**
- 不做 iOS/iPad/Vision 版（架构对齐，不在本期交付）。
- 不重写后端、不替换 Hermes 的 LLM 调用路径、不直连 LLM。
- 不追求与 Apple 真 Siri 的系统级功能对等，只复刻 **外观** + 接 Echo agent。

---

## 2. 系统架构

```
┌──────────────────────── EchoSiri.app (Swift 6 / SwiftUI) ───────────────────────────┐
│  View 层 (siri-app-ui-plan.md)                                                        │
│  ConversationGallery · TranscriptScroll · AskSiriInputBar · EchoSignalOverlay         │
│       ▲ @Observable 绑定                                                               │
│  ViewModel / Store 层                                                                  │
│  AppStore · ConversationListStore · ConversationStore · EchoSignalStore · TurnStore   │
│       ▲ async/await + Combine/AsyncStream                                             │
│  服务层                                                                                │
│  ├─ GatewayClient ── stdio NDJSON JSON-RPC ─┐  对话/工具/审批/clarify/语音            │
│  │     (StdioSubprocessTransport spawn)     │                                         │
│  ├─ EchoAPIClient   ── HTTP REST ────────────┤  Echo 信号/反馈/scope/M1/置信度        │
│  └─ BackendLocator ── 定位 python+repo ──────┘  (探活/拉起依决策 §14.2)               │
└──────────┬───────────────────────────────────┼───────────────────────────────────────┘
   spawn   │ stdin/stdout 管道                  │  http://127.0.0.1:9119/api/plugins/echo_signals/*
           ▼                                    │  (仅 Echo 信号需要 dashboard 在跑)
                  ┌─────────────────────────────┴──────────────────────────────┐
                  │  Echo 后端                                                    │
                  │  ┌────────────────────────┐   ┌──────────────────────────┐   │
                  │  │ python -m              │   │ Hermes Dashboard (FastAPI)│   │
                  │  │   tui_gateway.entry    │   │  /api/plugins/echo_signals │   │
                  │  │  dispatch() + _emit()  │   │  (echo_signals 插件 REST) │   │
                  │  │  JSON-RPC (NDJSON/stdio)│   └──────────┬───────────────┘   │
                  │  └──────────┬─────────────┘              │                     │
                  │             └── AIAgent.run_conversation() (run_agent.py) ──┘   │
                  │                         │                                          │
                  │             sessions.db (Hermes 会话 + echo_* 信号表)             │
                  └────────────────────────────────────────────────────────────────┘
```

**核心判断（已 live 验证修正）：把 gateway 当 stdio 子进程拉起，不连 dashboard WS、不造新协议、零改 Hermes。**
- 实地核查发现：dashboard 的 `--tui` 走的是 `/api/pty`（PTY 流式终端 + xterm.js），**不是**干净 JSON-RPC；而 `ws.py` 的 `handle_ws` **当前根本没挂**。`hermes_cli/web_server.py` 是 Hermes upstream 文件（非 Echo 白名单），不应改去挂 WS。
- **正解（ui-tui 同款）**：`ui-tui/src/gatewayClient.ts:326` 用 `spawn(python, ['-m','tui_gateway.entry'], {stdio:['pipe','pipe','pipe']})` 把 gateway 当子进程，stdin 写、stdout 按行读 NDJSON。原生 App 完全照搬：`StdioSubprocessTransport` spawn 同样的进程 → **零后端改动、零 dashboard 依赖**（聊天链路）。
- 线协议：**双向 newline-delimited JSON-RPC**，进程启动即发 `gateway.ready` 事件（live 冒烟确认）。
- `ui-tui/src/app/createGatewayEventHandler.ts` 是 **行为金标准**，Swift 逐事件对照实现。
- WS（`ws.py`）作为 **可选备选**保留：若将来要远程/容器化部署，可在 Echo 自己的 dashboard 插件里挂 `@router.websocket("/ws")`（落在 `plugins/echo_signals/dashboard/`，白名单内），仍不碰 Hermes core。
- **唯一仍需 dashboard 的**：Echo 信号 REST（`/api/plugins/echo_signals/*`，§3.4）—— 聊天不需要，但评分/scope/M1 队列需要 dashboard 在跑（Phase 4 决定是否改走 gateway 方法以彻底解耦）。

---

## 3. 协议契约（精确）

### 3.1 线格式
- 传输：**stdio 子进程**（生产）——spawn `python -m tui_gateway.entry`，每行一条 JSON（NDJSON，send 追加 `\n`，receive 按行读）。WS 为可选备选，帧语义相同。
- **请求**（App→GW）：`{"jsonrpc":"2.0","id":<int>,"method":"<verb>","params":{...}}`
- **响应**（GW→App）：`{"jsonrpc":"2.0","id":<int>,"result":{...}}` 或 `{"jsonrpc":"2.0","id":<int>,"error":{...}}`
- **事件**（GW→App，无 id，单向推送）。**真实形状**（实读 `server.py:_emit` + live 冒烟确认，2026-06）：
  ```json
  {"jsonrpc":"2.0","method":"event","params":{
     "type":"<事件名>", "session_id":"<gateway sid>",
     "session_key":"<Hermes 真实会话 id, Echo 扩展>",
     "payload": { ...事件特定字段... }}}
  ```
  - ⚠️ 事件名在 `params.type`（**不是** `event`）；payload **嵌套**在 `params.payload`（**不是**平级兄弟键）。
  - `session_key` 是 Echo 在 `_emit` 里加的——评分/scope widget 必须用它（Hermes 会话 id）而非 gateway 内部 `session_id` 来定位会话（CLAUDE.md Step 23 的坑）。
  - 早期本文档误写为 `params.event` + 平级 payload；已由 live 冒烟纠正，Swift `EventMeta`/`EventParser` 按真实形状实现。

### 3.2 请求方法（method）× 响应类型

| 能力 | method | params 关键字段 | result 关键字段 |
|---|---|---|---|
| 列会话 | `session.list` | — | `sessions: [{id, title, preview, message_count, started_at, source}]` |
| 新建 | `session.create` | `cwd?`, `model?` | `session_id`, `info: SessionInfo` |
| 恢复/打开 | `session.resume` | `session_id` | `session_id`, `messages:[{role,text,name?,context?}]`, `message_count`, `info` |
| 关闭 | `session.close` | `session_id` | `ok` |
| 重命名 | `session.title` | `session_id`, `title?` | `title`, `pending`, `session_key` |
| 删除 | `session.delete` | `session_id` | `deleted` |
| 分支 | `session.branch` | `session_id`, … | `session_id`, `title` |
| 发消息 | `prompt.submit` | `session_id`, `text`, `images?` | `ok` |
| 后台任务 | `prompt.background` | `session_id`, `text` | `task_id` |
| 打断 | `session.interrupt` | `session_id` | `ok` |
| 停止 | `stop` | — | `killed` |
| 转向 | `session.steer` | `session_id`, `text` | `status:'queued'\|'rejected'`, `text` |
| 撤销 | `session.undo` | `session_id` | `removed` |
| 压缩 | `session.compress` | `session_id` | `before/after_messages`, `summary{…}`, `messages` |
| 用量 | `session.usage` | `session_id` | `Usage`（见 §5） |
| 状态 | `session.status` | `session_id` | `output` |
| 斜杠 | `slash.exec` | `name`, `args?` | `output`, `warning` |
| 澄清应答 | `clarify.respond` | `session_id`, `request_id`, `answer` | `ok` |
| 审批应答 | `approval.respond` | `session_id`, `request_id`, … | `resolved` |
| 语音开关 | `voice.toggle` | … | `enabled`, `available`, … |
| 录音 | `voice.record` | … | `status`, `text` |

> 完整 method 列表（已确认存在于 server.py）：`session.{list,create,resume,close,title,delete,branch,save,history,status,usage,interrupt,steer,undo,compress}` · `prompt.{submit,background}` · `clarify.respond` · `approval.respond` · `sudo.respond` · `secret.respond` · `slash.exec` · `stop` · `interrupt` · `voice.{toggle,record,tts,status,transcript}`。

### 3.3 事件（event）× payload 字段

| event | payload 字段 | UI 动作 |
|---|---|---|
| `gateway.ready` | — | 连接就绪，开始 `session.list` |
| `session.info` | `SessionInfo` | 更新模型/技能/用量元数据 |
| `message.start` | （空，或 rid） | 在 transcript 建一个空的助手气泡 |
| `message.delta` | `text`（增量）, `rendered?` | 追加到当前助手气泡（流式） |
| `message.complete` | `text`（全文）, `usage`, `status`, `reasoning?`, `warning?`, `rendered?` | 收尾，替换为最终全文 + 用量 |
| `tool.generating` | `name` | 「正在准备 <name>…」 |
| `tool.progress` | `name`, `preview` | 工具进度预览 |
| `tool.complete` | `tool_id`, `name`, `error?`, `summary?`, `duration_s`, `todos?`, `inline_diff?` | 折叠成结果摘要行 |
| `reasoning.delta` | `text` | 追加到「思考过程」折叠块 |
| `thinking.delta` | `text` | 同上（thinking 通道） |
| `reasoning.available` | — | 标记本轮有 reasoning |
| `status.update` | `kind`, `text` | 顶部状态条（"Thinking…/Running tool…"） |
| `clarify.request` | `question`, `choices[]`, `request_id` | **M1 提名确认卡** → 用户选 → `clarify.respond{answer}` |
| `approval.request` | `command`, `description`, `request_id` | 危险命令审批卡 |
| `sudo.request` / `secret.request` | `request_id` / `env_var,prompt,request_id` | 凭据输入卡 |
| `error` | `text` / `message` | 错误提示 |
| `skin.changed` | … | 皮肤热切换 |
| `voice.transcript` / `voice.status` | `text` / … | 语音转写 / 状态（Phase 2） |
| `browser.progress` | … | 浏览进度（Phase 2） |

> **关键链路**：`message.start → message.delta×N → message.complete` 是一轮回复；`clarify.request → clarify.respond` 是 Echo M1 主动提名（Step 25 的「full clarify round-trip 尚未 live 验证」正好由本 App 走通）。

### 3.4 Echo 信号 REST（已存在于 `plugin_api.py`，base = `/api/plugins/echo_signals`）

| 方法 | 端点 | 用途 |
|---|---|---|
| GET | `/skills` · `/skills/{id}/timeline` | 置信度排名 / 时间线 |
| GET | `/status` · `/status-distribution` | schema 版本/编码器/各表行数 · 状态分布 |
| GET | `/invocations/recent?session_id=` | 评分 widget 的当前轮次队列 |
| GET | `/scope/pending?session_id=` | 待回答的 M2 scope 问题 |
| POST | `/scope` | 提交 scope 选择 |
| POST | `/feedback` | 👍/👎 + reason（带 `invocation_id`） |
| GET | `/candidates` · `/candidates/sessions` | M1 候选（有技能 / skill-less） |
| GET / DELETE | `/preferences` · `/preferences/{id}` | M5 偏好库 |
| POST | `/clipboard-signal` | 剪贴板/窗口焦点信号（替代 Tauri 壳） |

> session 关联：`/invocations/recent` 与 `/scope/pending` 的 `session_id` 需传 **gateway 的 `session_key`**（agent live session id），不是 events 流里的内部 id——CLAUDE.md Step 23 已踩过这个坑，Swift 侧从 `session.create/resume` 的 `info`/`session_key` 取真值。

---

## 4. Swift 工程结构与分层

起 **新 Xcode 工程**（不 fork macai，避免继承其 Core Data / APIHandler 包袱），**选择性拷贝** macai 的纯 UI 工具类。

```
desktop/EchoSiri/                      # 纳入 CLAUDE.md「Echo 五处代码」白名单
├─ EchoSiri.xcodeproj
├─ EchoSiri/
│  ├─ App/
│  │  ├─ EchoSiriApp.swift             # @main, WindowGroup, .hiddenTitleBar
│  │  ├─ AppDelegate.swift             # NSWindow 定制(透明标题栏/可拖拽), 生命周期
│  │  └─ Menus.swift                   # 菜单栏 Conversation 菜单 (CommandMenu)
│  ├─ DesignSystem/
│  │  ├─ Tokens.swift                  # 圆角/间距/字体/色板
│  │  ├─ GlassStyles.swift             # glassCard() / GlassButtonStyle / 26→旧降级
│  │  └─ Theme.swift                   # 明暗 + Echo sonar-teal
│  ├─ Models/                          # §5 全部 Codable
│  │  ├─ GatewayEnvelope.swift         # JSON-RPC 请求/响应/事件信封
│  │  ├─ GatewayEvents.swift           # 各 event payload
│  │  ├─ GatewayResponses.swift        # 各 method 的 result
│  │  ├─ SessionModels.swift           # SessionInfo / SessionListItem / Usage / TranscriptMessage
│  │  └─ EchoModels.swift              # invocation / scope / candidate / preference / feedback
│  ├─ Services/
│  │  ├─ GatewayClient.swift           # §6
│  │  ├─ GatewayProtocol.swift         # method 常量 + 编解码
│  │  ├─ EchoAPIClient.swift           # §7
│  │  ├─ BackendSupervisor.swift       # 探活/拉起/停止 (依决策 §14.2)
│  │  └─ Reconnector.swift             # 指数退避重连
│  ├─ Stores/                          # @Observable (Swift 5.9+ Observation)
│  │  ├─ AppStore.swift                # 连接态/全局
│  │  ├─ ConversationListStore.swift   # 侧栏画廊数据
│  │  ├─ ConversationStore.swift       # 单会话 transcript + 流式
│  │  ├─ TurnStore.swift               # 当前轮的 tool/reasoning/status 聚合
│  │  └─ EchoSignalStore.swift         # 评分/scope/clarify/候选/偏好
│  ├─ Views/
│  │  ├─ RootSplitView.swift
│  │  ├─ Sidebar/                      # ConversationGallery, ConversationCard, MasonryLayout, SidebarToolbar
│  │  ├─ Conversation/                 # ConversationPane, GlassToolbar, TranscriptScroll,
│  │  │                                #   UserBubble, AssistantResponse, InlineImageCard,
│  │  │                                #   SourceChips, ToolActivityRow, ReasoningBlock, AskSiriInputBar
│  │  ├─ Echo/                         # SignalOverlay, RatingWidget, ScopeQuestionCard, ClarifyCard, EchoSidePanel
│  │  ├─ RichText/                     # MarkdownRenderer, CodeBlockView   (借 macai MessageParser/HighlightedText)
│  │  └─ Welcome/                      # WelcomeScreen (空态, 可选)
│  └─ Resources/                       # Assets.xcassets, AppIcon, Localizable (zh-Hans/en)
├─ EchoSiriTests/                      # 协议层单测 + 模型解码
└─ EchoSiriUITests/                    # XCUITest 关键路径 (可选)
```

**从 macai 选择性拷贝（去依赖后）**：`MessageParser.swift`、`HighlightedText.swift`、`ThinkingProcessView.swift`（→ ReasoningBlock 参考）、`ZoomableImageView.swift`、`AttachmentParser.swift`。**不拷**：任何 `APIHandlers/*`、`ChatStore`/Core Data、`APIService*`。

---

## 5. 核心数据模型（Codable）

直接对照 `gatewayTypes.ts` / `types.ts` 翻译。示例（节选，命名用 `CodingKeys` 处理 snake_case）：

```swift
// JSON-RPC 信封
struct RPCRequest<P: Encodable>: Encodable {
    let jsonrpc = "2.0"; let id: Int; let method: String; let params: P
}
struct RPCResponse<R: Decodable>: Decodable {
    let id: Int; let result: R?; let error: RPCError?
}
struct RPCError: Decodable { let code: Int; let message: String }

// 事件信封：先解 event 名，再按名解 payload
struct EventEnvelope: Decodable {
    let event: String           // params.event
    let sid: String?
    // payload 用 keyed container 动态取，见 GatewayClient 解码
}

// 会话
struct SessionListItem: Decodable, Identifiable {
    let id: String
    let title: String
    let preview: String
    let messageCount: Int       // message_count
    let startedAt: Double       // started_at (epoch)
    let source: String?
}
struct SessionInfo: Decodable {
    let model: String
    let skills: [String:[String]]
    let tools: [String:[String]]
    let usage: Usage?
    let version: String?
    let cwd: String?
    let profileName: String?    // profile_name
    // …(release_date, reasoning_effort, fast, lazy, mcp_servers)
}
struct TranscriptMessage: Decodable, Identifiable {
    let id = UUID()
    let role: Role              // user|assistant|system|tool
    let text: String?
    let name: String?
    let context: String?
    enum CodingKeys: String, CodingKey { case role, text, name, context }
}
enum Role: String, Decodable { case user, assistant, system, tool }
struct Usage: Decodable {
    let input, output, total, calls: Int
    let costUsd: Double?        // cost_usd
    let contextPercent: Double? // context_percent
    // …
}

// 流式事件 payload
struct MessageDelta: Decodable { let text: String; let rendered: String? }
struct MessageComplete: Decodable {
    let text: String; let usage: Usage?; let status: String
    let reasoning: String?; let warning: String?
}
struct ToolComplete: Decodable {
    let toolId: String?; let name: String?; let error: String?
    let summary: String?; let durationS: Double?     // duration_s
    enum CodingKeys: String, CodingKey { case toolId="tool_id", name, error, summary, durationS="duration_s" }
}
struct ClarifyRequest: Decodable {
    let question: String; let choices: [String]; let requestId: String  // request_id
}

// Echo 信号 (REST)
struct EchoInvocation: Decodable, Identifiable {
    let id: Int; let skillId: String?; let skillName: String?
    let rated: Bool; let sessionId: String?
}
struct ScopePending: Decodable { let skillId: String; let skillName: String; let sessionId: String }
struct EchoCandidate: Decodable, Identifiable { let id: Int; let score: Int; let reasons: [String] }
struct FeedbackBody: Encodable {
    let invocationId: Int; let rating: Int; let reason: String?; let sessionId: String?
}
```

> 完整字段以 §3 表 + 源 `gatewayTypes.ts` 为准；**用一组 fixture JSON（录自真后端）跑解码单测**，防字段漂移。

---

## 6. GatewayClient 设计

职责：维持一条 WS、收发 JSON-RPC、请求/响应配对、事件分发、重连。

**接口**
```swift
@Observable final class GatewayClient {
    enum State { case disconnected, connecting, ready, reconnecting, failed(Error) }
    private(set) var state: State = .disconnected

    func connect(url: URL) async                    // 连接 + 等 gateway.ready
    func call<R: Decodable>(_ method: String, _ params: Encodable) async throws -> R
    var events: AsyncStream<GatewayEvent>           // 事件总线，Stores 订阅
    func disconnect()
}
```

**实现要点**
- 底层 `URLSessionWebSocketTask`；收到的每个文本帧解析为 JSON：
  - 有 `id` + (`result`|`error`) → 响应：用 `id` 找到挂起的 `CheckedContinuation` 完成它。
  - `method=="event"` → 取 `params.event` 派发到 `events` 流。
  - `method=="event"` 且 `event=="gateway.ready"` → 把 `state` 置 `.ready`，放行 `connect()` 的等待。
- 请求 id：`AtomicInt` 自增；`[id: CheckedContinuation]` 字典 + actor 隔离，避免数据竞争。
- **超时**：每个 `call` 包 `Task` + `withTimeout`（默认 30s，长任务如 `prompt.submit` 立即返回 `{ok}`，真正输出走事件，所以 call 本身不会久等）。
- **背压**：`message.delta` 高频，事件流用 `.bufferingNewest` 策略，UI 端做合批（每 ~16ms flush 一次到 transcript）。
- **线程**：网络在后台，事件投递切到 `@MainActor`（Stores 是 main-actor 隔离）。
- **金标准对照**：逐 `case` 比对 `createGatewayEventHandler.ts` 的处理，确保字段读取、容错（`ev.payload.text ?? ''`）一致。

---

## 7. EchoAPIClient 设计

普通 REST：`URLSession` + `async/await` + `Codable`。

```swift
struct EchoAPIClient {
    let base: URL    // http://127.0.0.1:9119/api/plugins/echo_signals
    func recentInvocations(sessionId: String) async throws -> [EchoInvocation]
    func pendingScope(sessionId: String) async throws -> [ScopePending]
    func submitScope(skillId: String, level: String, sessionId: String) async throws
    func sendFeedback(_ body: FeedbackBody) async throws
    func candidates(limit: Int, minScore: Int) async throws -> [EchoCandidate]
    func sessionCandidates() async throws -> [EchoCandidate]
    func preferences() async throws -> [Preference]
    func deletePreference(_ id: Int) async throws
    func status() async throws -> EchoStatus
    func clipboardSignal(_ body: ClipboardSignalBody) async throws
}
```
- 轮询：评分队列/scope 用 5s 轮询（对齐 dashboard bundle 现有节奏），或在 `message.complete` 后主动拉一次（更省、更及时）——推荐 **事件驱动拉取**（收到回复完成 → 拉 `/invocations/recent`）。
- 幂等：所有写端点后端已幂等；前端维护「本会话已答」集合防闪烁（对齐 dashboard bundle 做法）。

---

## 8. 关键时序流

**A. 启动 → 列会话**
```
App 启动 → BackendSupervisor.ensureRunning() → GatewayClient.connect(ws://…/api/ws)
  ← event gateway.ready
→ call session.list  ← {sessions:[…]}  → ConversationListStore 填充画廊
（并行）EchoAPIClient.status() → StatusStrip
```

**B. 发一条消息（核心流式）**
```
用户在 AskSiriInputBar 回车
→ call prompt.submit{session_id, text}   ← {ok:true}（立即）
ConversationStore 立即插入 UserBubble + 占位 AssistantResponse
  ← event message.start                  → 标记助手气泡开始
  ← event status.update{Thinking…}       → 顶部状态条
  ← event reasoning.delta×N              → ReasoningBlock 累积（折叠）
  ← event tool.generating{name}          → ToolActivityRow「准备 name…」
  ← event tool.progress{name,preview}    → 进度预览
  ← event tool.complete{name,summary…}   → 折叠成结果行
  ← event message.delta×N {text}         → AssistantResponse 流式追加（合批 16ms）
  ← event message.complete{text,usage…}  → 替换为最终全文 + Usage + SourceChips
→ (事件驱动) EchoAPIClient.recentInvocations(sessionId) → 显示 RatingWidget
```

**C. M1 提名（clarify 往返）—— Echo 关键链路**
```
（agent 内部 m1_nomination 判定需提名）
  ← event clarify.request{question, choices:[A,B], request_id}
→ EchoSignalStore 弹 ClarifyCard（原生玻璃卡，question + 选项按钮）
用户点选
→ call clarify.respond{session_id, request_id, answer}  ← {ok}
（agent 据答案决定是否 skill_manage create）
```

**D. M2 scope（技能新建后）**
```
（agent skill create 后，scope_dialog 写 pending 行）
→ (轮询/事件后) EchoAPIClient.pendingScope(sessionId) ← [{skillId,…}]
→ ScopeQuestionCard「A 复用整套 / B 只复用大致想法」
用户选 → submitScope(skillId, level, sessionId)
```

**E. 打断**
```
推理中用户点 ⏹ → call session.interrupt{session_id} ← {ok}
  ← event message.complete{status:'interrupted'} → 收尾标记中断
```

---

## 9. 任务级 WBS（可勾选）

> 每个 Phase = 一个或多个 commit 边界（遵守仓库「每步提交+推送」规则；commit 前先把 diff 给维护者看）。括号内为粗估人日（单人，熟悉 SwiftUI）。

### Phase 0 — 立项 & 脚手架（2–3d）
- [ ] 确认 §14 全部决策点（用 AskUserQuestion）
- [ ] grep dashboard 是否已挂 `/api/ws`，定后端是否需补一行
- [ ] 通读 `createGatewayEventHandler.ts`，落成 §3 契约的最终核对表
- [ ] 建 `desktop/EchoSiri/` Xcode 工程（Swift 6, macOS 26 target，App Sandbox 评估）
- [ ] `DesignSystem/`：Tokens + GlassStyles（含 `#available(macOS 26)` 降级封装）
- [ ] 拷入并去依赖 macai 的 MessageParser / HighlightedText / ThinkingProcessView / ZoomableImageView
- [ ] 空壳 App 起得来，窗口呈 Liquid Glass（hiddenTitleBar + 材质背景）
- **退出**：`xcodebuild` 通过，窗口是玻璃骨架

### Phase 1 — 全 UI（mock 数据）（5–7d）
- [ ] `RootSplitView`：NavigationSplitView 三栏骨架
- [ ] Sidebar：`MasonryLayout`（双列贪心分配）+ `ConversationCard`（时间戳/置顶/标题/摘要/缩略图）+ `SidebarToolbar`
- [ ] Conversation：`TranscriptScroll` + `UserBubble`(pill) + `AssistantResponse`(富文本) + `InlineImageCard` + `SourceChips`
- [ ] `ToolActivityRow` + `ReasoningBlock`（折叠）+ `GlassToolbar`（search/title/overflow）
- [ ] `AskSiriInputBar`（浮起玻璃 pill：＋/field/🎙️）
- [ ] Echo：`RatingWidget` + `ScopeQuestionCard` + `ClarifyCard`（mock）
- [ ] 每个组件配 `#Preview`（空态/长文/带图/流式中）
- [ ] 菜单栏 Conversation 菜单（mock 动作）
- **退出**：对照参考图像素级走查通过；mock 流式动画顺滑

### Phase 2 — 协议层（4–6d）
- [ ] §5 全部 Codable 模型 + 录制的 fixture JSON 解码单测
- [ ] `GatewayClient`：WS 连接、JSON-RPC 编解码、req/resp 配对（actor）、事件流、超时
- [ ] `gateway.ready` 等待、id 自增、容错读字段
- [ ] `EchoAPIClient`：全部 REST 端点 + Codable
- [ ] `BackendSupervisor`：探活（GET `/`）+ 拉起/停止（依决策 §14.2）
- [ ] （若需）后端补 `@app.websocket("/api/ws")` 一行 + 跑 Echo/Hermes 回归确认 0 退化
- [ ] 协议层契约测试：录/放 JSON-RPC 帧（借鉴 `llm_cache.py` record/replay 思路）
- **退出**：单测/命令行跑通「连接→session.create→prompt.submit→收齐 message.* 流」

### Phase 3 — 集成（UI ↔ 真后端）（4–6d）
- [ ] Stores 订阅 `GatewayClient.events`，把 mock 源换成真事件
- [ ] 流式：message.start/delta/complete + 合批渲染（16ms）
- [ ] Agent 可视化：tool.* / reasoning.* / thinking.* / status.update 映射到 UI
- [ ] 画廊接 `session.list/history`；打开接 `session.resume`（回放历史 messages）
- [ ] 打断/停止/重命名/删除/分支接齐
- [ ] session_key 正确取值（避免 Step 23 的内部 id 坑）
- **退出**：用新 App 与 Echo agent 真实多轮对话，含工具调用可视化、历史恢复

### Phase 4 — Echo 信号原生化（核心价值）（4–5d）
- [ ] `RatingWidget` → `POST /feedback`（含 60s 撤销/补充理由窗口 + invocation_id 精确归属）
- [ ] `ScopeQuestionCard` → `GET /scope/pending` + `POST /scope`（session 作用域）
- [ ] **`ClarifyCard` → `clarify.request` 原生渲染 → `clarify.respond`**（M1 提名全链路 live 验证）
- [ ] 可选 `EchoSidePanel`：置信度排名 / 候选队列 / 偏好库（复用 REST）
- [ ] 剪贴板/窗口焦点 → `NSPasteboard` 轮询 + `NSWindow` 通知 → `POST /clipboard-signal`（替代 Tauri 壳，依决策 §14.4）
- **退出**：评分/scope/clarify 全链路在原生 UI live；Echo 生命周期可见

### Phase 5 — 精修 / 健壮 / 打包（4–6d）
- [ ] morph 动效（glassEffectID）、滚动虚化（scrollEdgeEffect）、消息淡入
- [ ] 暗色模式 + A11y（VoiceOver、对比度，沿用 Echo skin 的 WCAG 习惯）
- [ ] 重连（指数退避）、断线/后端崩溃恢复、错误态 UI
- [ ] 性能：长对话 masonry 复用、流式不掉帧、大历史懒加载
- [ ] AppIcon（补 UI 计划提到的缺失 `.icns`）、本地化 zh-Hans/en
- [ ] Developer ID 签名 + notarize + dmg；（可选）Sparkle 自动更新
- [ ] `./echo app` 子命令（构建/拉起，自动确保后端在跑）
- **退出**：可分发 1.0；冒烟脚本全链路绿

---

## 10. 错误处理与生命周期

| 场景 | 处理 |
|---|---|
| WS 连接失败 / 中途断开 | `Reconnector` 指数退避（1→2→4→8s，封顶 30s）；UI 顶部「重连中…」横幅；恢复后 `session.list` 对账 |
| `call` 超时 | 抛 `GatewayError.timeout`；UI toast；可重试（幂等的才自动重试） |
| 后端进程未起 | `BackendSupervisor` 拉起（决策 §14.2）；拉起失败→引导页「请运行 `./echo dash --tui`」 |
| `error` 事件 | 在当前轮插入红色系统行（对照 TS `sys()` 处理） |
| `approval.request` / `sudo` / `secret` | 原生卡片，应答走对应 `*.respond`；超时（server 端 300s/120s）后端自处理 |
| App 退出 | `session.close` 当前会话 + 按决策 §14.2 决定是否停后端（参考 Tauri 壳 PID 清理 + `./echo stop`） |
| 双前端并发（Web Dashboard 同时开） | 两者写同一套 REST，后端幂等；不新增写路径；信号不重复计（invocation_id 去重） |
| 协议字段缺失/新增 | Codable 全部可选 + 默认值，未知 event 落 info 日志（对照 TS「protocol noise」处理），不崩 |

---

## 11. 测试策略（分层）

1. **模型解码单测**：录制真后端的 JSON 帧为 fixture，逐 `Decodable` 断言（防字段漂移）。
2. **协议层契约测试**：`GatewayClient` 对录制帧回放（mock WS）；验证 req/resp 配对、事件派发、容错。
3. **Store 逻辑单测**：喂事件序列，断言 transcript/turn 聚合结果（流式合批、tool 折叠、clarify 弹出）。
4. **SwiftUI 快照/Preview 走查**：每组件多态 Preview；关键页快照回归。
5. **集成冒烟**：脚本拉起 `./echo dash --tui` → App 连 → 跑「建会话→发消息→收流→评分→scope→clarify」全链路（对齐 `scripts/verify_echo.py` 风格，新增 `./echo app-smoke`）。
6. **后端回归保护**：本期 Python 改动至多一行 ws 挂载 → 跑 `tests/plugins/echo_signals/` + Hermes 回归确保 0 退化。
7. **XCUITest（可选）**：关键路径（发消息、打断、评分）UI 自动化。

---

## 12. 打包 / 分发 / 签名

- **签名**：Developer ID Application 证书；Hardened Runtime。
- **公证**：`notarytool submit` + `stapler staple`。
- **App Sandbox**：评估能否开（需访问本地 127.0.0.1 端口 + NSPasteboard）；若开需 `com.apple.security.network.client` entitlement；剪贴板读不需特殊 entitlement（用户态）。
- **分发**：dmg（对齐 macai）；可选 Homebrew cask + Sparkle 自动更新。
- **后端依赖**：App 需要 Echo 后端在跑——分发版需决定是「假定用户已装 Echo」还是「内嵌拉起脚本」（决策 §14.2 的延伸）。

---

## 13. 与现有 Echo 工程衔接

- **`./echo` 启动器**：新增 `./echo app`（构建/拉起原生 App，自动确保后端在跑），与 `chat/tui/dash/tauri/full` 并列。
- **代码白名单**：CLAUDE.md「Echo 五处代码」追加 `desktop/EchoSiri/`（如当初加 `tauri-shell/`），保持 `git diff upstream/main` 干净。
- **dashboard 改动**：若挂 `/api/ws`，落在 echo_signals dashboard 插件或 Hermes 既有 mount 点——**先确认现状再动**；有歧义按规则问。
- **皮肤一致**：Echo 信号区沿用 sonar-teal 主色，原生 App 与 CLI/TUI 视觉同源。
- **文档**：完成后在 CLAUDE.md「Phase 1 status」加节记录；更新 `DevPlan/launch-and-debug.md`。
- **重启规则**：凡改后端服务的部分，仍遵守「自己 `./echo dash --tui` 重启 + 提示维护者硬刷新」。

---

## 14. 开放决策点（动工前需维护者拍板）

> 这些是 CLAUDE.md 规则 3 要求「有多个合理答案就问」的点。建议用 AskUserQuestion 一次性收齐。

1. **最低系统**：只支持 macOS 26+（外观纯净、省一套 fallback）vs 兼容 14/15（多一套降级）。
2. **后端形态**：App **自动拉起** Echo 子进程（开箱即用，像 Tauri 壳）vs **连接已运行** 的 `./echo dash`（开发期方便，分发需另说）。
3. **工程位置**：同仓 `desktop/EchoSiri/`（与 `tauri-shell/` 并列，纳入白名单）vs 独立 repo。
4. **Tauri 壳去留**：原生 App 是否取代 `tauri-shell/`（剪贴板/窗口焦点改用 `NSPasteboard`/`NSWindow` 重新实现）。
5. **Echo 面板范围（MVP）**：只做对话内 评分+scope+clarify，还是含 置信度/候选/偏好 侧面板。
6. **附件/语音范围**：MVP 是否含图片/PDF 附件、是否含语音（`voice.*` gateway 已具备，但是 Phase 2 量级）。

---

## 15. 里程碑与退出标准

| 里程碑 | Phase | 退出标准 | 粗估 |
|---|---|---|---|
| M0 脚手架 | 0 | App 能起，窗口 Liquid Glass | 2–3d |
| M1 全 UI | 1 | 对照参考图像素级走查通过（mock） | 5–7d |
| M2 协议层 | 2 | 单测跑通建会话/发消息/收流 | 4–6d |
| M3 集成 | 3 | 真实多轮对话 + 工具可视化 + 历史恢复 | 4–6d |
| M4 Echo 信号 | 4 | 评分/scope/clarify 全链路 live | 4–5d |
| M5 精修打包 | 5 | 签名 notarize 的 dmg；A11y/暗色/重连达标 | 4–6d |

**合计粗估 ≈ 23–33 人日**（单人）。UI（M1）与协议层（M2）可并行（mock 隔离），实际 wall-clock 更短。

---

## 下一步（动工前）

1. `AskUserQuestion` 确认 §14 的 6 个决策点。
2. grep 核查 dashboard 是否已暴露 `/api/ws`。
3. 通读 `ui-tui/src/app/createGatewayEventHandler.ts`，定稿 §3/§5 契约。
4. 起 `desktop/EchoSiri/`，进入 Phase 0。

> 参考与来源见 [siri-app-ui-plan.md](siri-app-ui-plan.md) 末尾「Sources」。
