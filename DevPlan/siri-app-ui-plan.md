# Echo Desktop —「Siri-look」原生 App · UI 开发计划

> 目标：用 Apple 原生工具链（SwiftUI / AppKit / UIKit-on-Mac）做一个 **外观上与 macOS 27 "Golden Gate" 新 Siri App 几乎一致** 的桌面应用，采用最新 **Liquid Glass** 设计语言；后端复用 Echo 的 Agent Harness（Hermes + echo_signals），前端通过 gateway 协议接入。
>
> 本文件只覆盖 **UI 层**。工程化、后端接入、里程碑见 [desktop-app-dev-plan.md](desktop-app-dev-plan.md)。
>
> 参考实现：开源项目 [macai](https://github.com/Renset/macai)（原生 macOS SwiftUI AI 聊天客户端，`NavigationSplitView` + Core Data 架构，已 clone 到 `/tmp/macai_ref` 研究过）。

---

## 1. 截图特征拆解（参考图 = 新 Siri App）

逐区域记录，作为像素级复刻的 spec。坐标按原图 2060×1314 描述。

### 1.1 窗口与材质
- **整窗 Liquid Glass**：大圆角（≈ 16–20pt），窗口半透明，暖色壁纸从边缘透出；内容区是一层"磨砂玻璃"而非纯白。
- **无可见标题栏**：traffic lights（红/黄/绿）浮在内容之上，`titlebarAppearsTransparent + .hiddenTitleBar`，工具栏图标与内容同层。
- **分栏**：左 sidebar ≈ 占窗宽 22%，右主对话区占其余；两栏之间无硬分割线，靠材质明暗过渡。

### 1.2 顶部工具栏
- 左侧：traffic lights → 紧接一个 **筛选/list 图标**（≡，圆角玻璃按钮）→ 一个 **新建/compose 图标**（带笔的方框）。
- 右侧：一个 **搜索放大镜**图标。
- 全部是 **无边框玻璃圆形/圆角按钮**，hover 才显描边。

### 1.3 左侧会话历史（最显著的差异点）
- **双列错落卡片网格**（staggered / masonry，非等高列表）——这是新 Siri 区别于传统聊天 App 的标志。
- 每张卡片：
  - 顶部小字 **时间戳**（`Monday` / `9:41 AM` / `Yesterday`）；置顶项有 📌 图钉。
  - **标题**（粗体，1–2 行，如 "Mexico City Largest Park"）。
  - **预览摘要**（次级灰字，3–4 行，省略号截断）。
  - 部分卡片带 **缩略图**（绘画、人物黑白照、蘑菇微距、蓝色颜料块）——图文混排卡片。
- 卡片为 **白/浅玻璃圆角矩形**，彼此间距 ≈ 12pt，轻微投影。
- 视觉上像 Apple Notes 的画廊视图 + Spotlight 卡片的混合。

### 1.4 主对话区（transcript）
- **用户气泡**：右对齐，浅灰圆角 pill，单行/短文（"What's the largest park in Mexico City?"）。
- **助手回复**：左对齐、**无气泡**，直接铺在玻璃上，富文本：
  - 首段普通正文。
  - **内联图片卡**（圆角、横幅式，带轻投影）。
  - **大标题**（衬线体 "Bosque de Chapultepec"）+ 正文 + **圆点列表**。
  - 底部 **来源归属** chip（"Wikipedia +2"）——可点开引用。
- 滚动时顶部内容 **在玻璃下虚化淡出**（content scrolls under glass toolbar）。

### 1.5 底部输入条
- **浮起的玻璃 pill**："Ask Siri" 占位符。
- 左侧 **＋ 按钮**（附件/动作），右侧 **麦克风按钮**。
- 悬浮在内容之上，底部留白，玻璃高光描边。

### 1.6 菜单栏
- `Siri  File  Edit  View  Conversation  Window  Help` —— 注意有自定义的 **Conversation** 菜单（新建/分支/删除/置顶会话等）。

### 1.7 配色 / 字体
- 中性暖灰 + 大量留白；强调色克制（来源 chip、selected 态才上色）。
- 系统 **SF Pro**（正文）+ 衬线 **New York**（回复里的大标题）——Apple 富文本回复的典型搭配。
- 注意：参考图日期是 `Wed Apr 1`，属概念/演示图，实复刻以 Liquid Glass HIG 规范为准、截图为风格目标。

---

## 2. 新 Siri App 调研结论（网络）

- **Liquid Glass** 是 Apple 自 iOS 7 以来最大的设计语言变更，WWDC 2025 发布，随 iOS 26 / macOS 26 Tahoe 落地；macOS 27 "Golden Gate"（WWDC 2026）继续打磨。核心：模拟真实玻璃的 **半透明、折射、深度、动态光感**，随内容/光线/交互自适应。
- **Siri 重设计**：更口语化、理解个人上下文、可代执行动作，并获得 **独立 App** 可恢复历史对话 —— 正是参考图所示形态（左侧历史 + 右侧富文本对话）。
- **开发者采用成本低**：用 **Xcode 26** 重新编译即自动获得新外观；标准控件（按钮/开关/工具栏/sidebar）自动套用 Liquid Glass。
- **关键新 API**（SwiftUI）：
  - `.glassEffect(_:in:isEnabled:)` —— 给自定义视图加玻璃材质，可定形状、可 tint。
  - `GlassEffectContainer { … }` —— 把多个玻璃形状合并渲染，支持互相 morph、统一光照、提升性能（**玻璃不能采样玻璃**，所以同组必须放进 container）。
  - `.glassEffectID(_:in:)` + `@Namespace` —— 玻璃元素之间的流体 morph 过渡。
  - 工具栏/sidebar 的新 spacing、`.toolbar` 增强、`backgroundExtensionEffect` 等。

> 来源见本文件末尾「Sources」。

---

## 3. macai 借鉴矩阵（哪些直接抄、哪些重写）

macai 结构（`/tmp/macai_ref`）：`macaiApp.swift` → `ContentView`（`NavigationSplitView`：`ChatListView` 侧栏 + `ChatView` 详情 + `WelcomeScreen`）；`UI/Chat/*`（输入条、消息流、思考过程、HTML 预览）；`Utilities/APIHandlers/*`（多家 LLM 适配）；Core Data `ChatStore`。

| macai 模块 | 对 Echo 的处置 |
|---|---|
| `ContentView` 的 `NavigationSplitView` 三栏骨架 | **借鉴骨架**，但侧栏换成「双列错落卡片网格」 |
| `ChatListView` / `ChatListRow`（等高列表行） | **重写** 成 masonry 卡片（见 §4.2） |
| `ChatView` / `ChatMessagesView`（气泡流） | **借鉴** 流式渲染 + Markdown 解析；改成「用户 pill / 助手无气泡富文本」 |
| `ChatInputView` / `ChatBottomContainerView`（输入条 + 附件） | **借鉴** 交互骨架，外观换成浮起玻璃 pill |
| `ThinkingProcessView`（推理过程折叠） | **直接复用思路** 映射到 Echo `reasoning.delta`/`thinking.delta` |
| `MessageParser` / `HighlightedText`（Markdown + 代码高亮） | **借鉴**，是富文本回复的基础 |
| `Utilities/APIHandlers/*`（OpenAI/Claude/Ollama…） | **整体丢弃** —— Echo 不直连 LLM，改走 gateway（见 dev-plan §后端接入） |
| Core Data `ChatStore` | **整体丢弃/降级** —— 会话真相在 Hermes `sessions.db`，本地只做 UI 缓存 |
| `PreferencesView` 多 tab | **借鉴** 设置面板骨架，内容换成 Echo/aux-model/endpoint 配置 |
| `WelcomeScreen` + 粒子动效 | **可选保留** 作为空态 |

**结论**：macai 给我们「原生 macOS 多栏聊天客户端」的成熟骨架与流式/Markdown 经验；UI 表层（卡片网格、玻璃、气泡形态）全部按 Siri 风格重做，后端层完全替换为 Echo gateway。

---

## 4. UI 组件树与实现要点

```
EchoSiriApp (App)
└─ MainWindow (WindowGroup, .hiddenTitleBar, Liquid Glass background)
   └─ RootSplitView : NavigationSplitView
      ├─ Sidebar:  ConversationGallery        ← 双列错落卡片
      │            └─ ConversationCard ×N      (.glassEffect, 含可选缩略图)
      │            上方: SidebarToolbar (filter / compose)
      └─ Detail:   ConversationPane
                   ├─ GlassToolbar (search, title, overflow)   浮于顶
                   ├─ TranscriptScroll
                   │   ├─ UserBubble (right, gray pill)
                   │   └─ AssistantResponse (left, no bubble)
                   │       ├─ RichText (SF Pro + New York headings)
                   │       ├─ InlineImageCard
                   │       ├─ BulletList
                   │       ├─ ToolActivityRow   ← Echo tool.*/reasoning.*
                   │       └─ SourceChips ("Wikipedia +2")
                   ├─ EchoSignalOverlay  ← 评分/scope/M1 提示（见 §4.6）
                   └─ AskSiriInputBar (floating glass pill: + / field / mic)
```

### 4.1 玻璃骨架（基础设施）
- `WindowGroup` + `.windowStyle(.hiddenTitleBar)`；`NSWindow.titlebarAppearsTransparent = true`、`isMovableByWindowBackground = true`。
- 整窗背景：`.background(.ultraThinMaterial)` 兜底；macOS 26+ 用 `backgroundExtensionEffect` 让壁纸延伸进窗体。
- 把分组的玻璃控件包进 **一个** `GlassEffectContainer`（同组才能正确 morph，性能也好）。
- **降级策略**（关键）：用 `if #available(macOS 26, *)` 包裹 `.glassEffect`；旧系统回落到 `.ultraThinMaterial` + 自绘描边。封一个 `GlassButtonStyle` / `glassCard()` ViewModifier 统一开关，避免散落的可用性判断。

### 4.2 ConversationGallery（双列错落卡片）—— 重头戏
- 用 **两个 `LazyVStack` 并排**（手动 masonry：维护 `leftColumn` / `rightColumn`，按累计高度贪心分配），或 macOS 26 的瀑布流布局 API；卡片高度由内容（有无缩略图、摘要行数）决定。
- `ConversationCard`：圆角玻璃卡，置顶 📌、时间戳、标题、灰字摘要、可选 `AsyncImage` 缩略图；selected 态上 accent 描边 + 轻微放大。
- 数据来自 Echo `session.list`（标题 / updatedDate）+ `session.history`（取首条 user/assistant 摘要 + 可能的图片附件）。
- 顶部 `SidebarToolbar`：filter（按时间/置顶）、compose（`session.create`）。
- 交互：单击 → `session.resume` 打开；右键菜单 → 置顶/重命名(`session.title`)/删除(`session.delete`)/分支(`session.branch`)。

### 4.3 TranscriptScroll（对话流）
- `ScrollView` + `LazyVStack`，`scrollTargetBehavior` 让新消息吸底；顶部内容 **滚到玻璃工具栏下虚化**（`.scrollEdgeEffectStyle` / 渐隐 mask）。
- **UserBubble**：右对齐、`Capsule`/大圆角、`secondary` 底色。
- **AssistantResponse**：左对齐、无底色，富文本：
  - Markdown → AttributedString；`# 标题` 渲染为 New York 衬线；列表、代码块（借 macai `MessageParser`/`HighlightedText`）。
  - `InlineImageCard`：圆角横幅 + QuickLook 放大（借 macai `ZoomableImageView`）。
  - `SourceChips`：来源胶囊，`+N` 可展开。
- 流式：`message.start` 建气泡 → `message.delta` 追加 → `message.complete` 收尾；打字机/淡入动画。

### 4.4 ToolActivity / Reasoning（Hermes Agent 特有）
- Echo 是 **Agent**（会调工具、有 reasoning），比纯聊天复杂。映射 gateway 事件：
  - `tool.generating` / `tool.progress` / `tool.complete` → 一行可折叠「🔧 正在执行 <tool>…」活动条，完成后折叠为结果摘要。
  - `reasoning.delta` / `thinking.delta` → 折叠的「思考过程」块（借 macai `ThinkingProcessView`）。
  - `status.update` → 顶部细状态条（"Thinking…/Running tool…"）。
- 视觉上用 **更淡的玻璃 + 单色图标**，不与最终回复抢视觉重量。

### 4.5 AskSiriInputBar（浮起玻璃输入条）
- 浮于底部的玻璃 pill：左 `＋`（附件/动作菜单）、中多行自适应 `TextField`（"Ask Siri"）、右 🎙️。
- 发送 → `prompt.submit`；推理中 🎙️ 变 ⏹（`session.interrupt` / `stop`）。
- 附件（图/PDF）借 macai `AttachmentParser` 思路；是否支持取决于 gateway/主模型能力。
- 语音（可选 Phase 2）：`voice.toggle` / `voice.record` / `voice.transcript` / `voice.tts` gateway 已具备。

### 4.6 EchoSignalOverlay —— Echo 的灵魂，必须进 UI
Echo 的全部价值在「用户信号驱动的技能生命周期」。Web Dashboard 上的那套交互要 **原生化** 进对话区：
- **评分 widget**（对应 dashboard `chat:bottom`）：每轮回复下方 👍/👎；点后 60s 撤销/补充理由窗口；理由走 `POST /feedback` + reason-LLM 打分。
- **M2 Scope 问题**：技能新建后弹「复用整套思路 / 只复用大致想法」二选一（`GET /scope/pending` → `POST /scope`）。
- **M1 提名 / clarify**：技能孵化提名通过 gateway **`clarify.request` → `clarify.respond`** 原生渲染成一张确认卡（"要不要把这套做法存成技能？"）——这是 Echo 主动提名的关键链路，gateway 已接好。
- **侧边 Echo 面板（可选）**：置信度排名、候选队列、偏好库，复用 dashboard 的 REST（`/api/plugins/echo_signals/*`）。

> 两条后端通道：**WebSocket JSON-RPC（对话 + clarify/approval）** + **REST（Echo 信号/反馈/M1 队列）**。详见 dev-plan。

---

## 5. 设计令牌（Design Tokens）

| Token | 值 / 来源 |
|---|---|
| 圆角 | 窗口 18 / 卡片 14 / pill ∞(Capsule) / 按钮 10 |
| 材质 | macOS26: `.glassEffect`；回落 `.ultraThinMaterial` |
| 正文字体 | SF Pro (`.body` / `.callout`) |
| 标题字体 | New York (`.system(.title, design: .serif)`) |
| 间距 | 卡片 gutter 12、内容 padding 16、卡内 10 |
| 强调色 | 系统 accentColor（克制使用）；Echo 信号沿用 Echo sonar-teal 皮肤色 |
| 阴影 | 卡片 y=1 blur=4 低透明；玻璃靠高光描边而非重投影 |
| 动效 | morph 用 `glassEffectID` + `matchedGeometryEffect`；消息淡入 `.smooth` |

把以上集中到 `DesignSystem/Tokens.swift` + `GlassStyles.swift`，全局引用，便于一处调参。

---

## 6. UI 交付物清单

```
EchoSiri/
├─ App/                EchoSiriApp.swift, AppDelegate(NSWindow 定制), Menus(Conversation 菜单)
├─ DesignSystem/       Tokens.swift, GlassStyles.swift (glassCard / GlassButtonStyle / 降级)
├─ Sidebar/            ConversationGallery.swift, ConversationCard.swift, SidebarToolbar.swift, MasonryLayout.swift
├─ Conversation/       ConversationPane.swift, GlassToolbar.swift, TranscriptScroll.swift,
│                      UserBubble.swift, AssistantResponse.swift, InlineImageCard.swift,
│                      SourceChips.swift, ToolActivityRow.swift, ReasoningBlock.swift,
│                      AskSiriInputBar.swift
├─ Echo/               SignalOverlay.swift, RatingWidget.swift, ScopeQuestionCard.swift,
│                      ClarifyCard.swift, EchoSidePanel.swift
├─ RichText/           MarkdownRenderer.swift, CodeBlockView.swift (借 macai)
└─ Welcome/            WelcomeScreen.swift (空态, 可选)
```

UI 层先用 **mock 数据 / SwiftUI #Preview** 跑通全部外观与动效，再接后端（见 dev-plan 的阶段划分）。每个组件都配 `#Preview`，做到「无后端也能像素级走查」。

---

## 7. UI 阶段里程碑（仅 UI，工程全景见 dev-plan）

- **U0 玻璃骨架**：窗口 + `NavigationSplitView` + Liquid Glass 背景 + 降级封装 + 设计令牌。
- **U1 侧栏画廊**：masonry 卡片（mock 数据），置顶/选中/右键菜单。
- **U2 对话流**：用户 pill + 助手富文本 + 内联图 + 来源 chip + 输入条（mock 流式）。
- **U3 Agent 可视化**：tool / reasoning / status 活动条与折叠。
- **U4 Echo 信号原生化**：评分、scope、clarify 卡、可选侧面板（mock）。
- **U5 精修**：morph 动效、滚动虚化、暗色模式、A11y（VoiceOver/对比度，沿用 Echo skin 的 WCAG 习惯）、菜单栏 Conversation 菜单。

> 接后端后这些 mock 替换为真实 gateway/REST 数据，属 dev-plan 的集成阶段。

---

## Sources

- [Apple — Introduces a delightful and elegant new software design (Liquid Glass)](https://www.apple.com/newsroom/2025/06/apple-introduces-a-delightful-and-elegant-new-software-design/)
- [Meet Liquid Glass — WWDC25](https://developer.apple.com/videos/play/wwdc2025/219/)
- [Build a SwiftUI app with the new design — WWDC25](https://developer.apple.com/videos/play/wwdc2025/323/)
- [macOS 27 Golden Gate vs macOS 26 Tahoe — Macworld](https://www.macworld.com/article/3159860/macos-golden-gate-vs-macos-tahoe-whats-new-should-you-upgrade.html)
- [WWDC26 recap: Liquid Glass changes and Siri AI — Cult of Mac](https://www.cultofmac.com/news/wwdc26-recap)
- [LiquidGlassReference (Swift/SwiftUI) — conorluddy/GitHub](https://github.com/conorluddy/LiquidGlassReference)
- [Understanding GlassEffectContainer in iOS 26 — DEV](https://dev.to/arshtechpro/understanding-glasseffectcontainer-in-ios-26-2n8p)
- [macai — native macOS AI chat client (参考实现)](https://github.com/Renset/macai)
