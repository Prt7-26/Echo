# Echo Desktop App —— 线框图（Wireframes）

> 配合 [siri-app-ui-plan.md](siri-app-ui-plan.md) 与 [desktop-app-dev-plan.md](desktop-app-dev-plan.md) 使用。
> 纯 ASCII 线框，标注尺寸/材质/数据来源/对应 gateway 事件。坐标单位 pt（@1x），实际随窗口自适应。
> 所有图为 Echo 自绘布局（设计语言参考 Liquid Glass HIG，非拷贝 Apple 资源）。

图例：`▓`玻璃描边 · `░`磨砂材质 · `●`traffic light · `［ ］`玻璃按钮 · `‹glass›`玻璃容器 · `←evt` 数据来源

---

## W1 · 主窗口总览（默认态）

窗口 ≈ 1180×760，整窗 Liquid Glass、大圆角 18、隐藏标题栏。左栏 22%，右栏其余。

```
╭──────────────────────────────────────────────────────────────────────────────────╮ ← NSWindow .hiddenTitleBar
│ ● ● ●   ［≡］［✎］                                                         ［🔍］   │ ← GlassToolbar (浮于内容上)
│ filter compose                                                            search    │   ←session.create(✎)
├─────────────────────────┬──────────────────────────────────────────────────────────┤
│ SIDEBAR  (ConvGallery)  │  CONVERSATION PANE                                         │
│ 双列错落卡片 ░          │  TranscriptScroll ░                                        │
│ ┌─────────┐ ┌─────────┐ │                                                            │
│ │📌 Monday│ │ 9:41 AM │ │                            ╭───────────────────────────╮  │
│ │Healthy  │ │Mexico   │ │                            │ What's the largest park…? │░ │ ← UserBubble (右, pill)
│ │30-Min   │ │City     │ │                            ╰───────────────────────────╯  │
│ │Recipes  │ │Largest  │ │                                                            │
│ │You can… │ │Park     │ │  The largest park in Mexico City is Bosque de Chapul-     │ ← AssistantResponse
│ └─────────┘ │ ┌─────┐ │ │  tepec. Covering over 1,695 acres…                        │   (左, 无气泡)
│ ┌─────────┐ │ │ img │ │ │  ┌──────────────────────────────────────────────────┐    │
│ │ 9:27 AM │ │ └─────┘ │ │  │  [ inline image card · 圆角 · 轻投影 ]            │░   │ ← InlineImageCard
│ │Social   │ └─────────┘ │  └──────────────────────────────────────────────────┘    │
│ │Media    │ ┌─────────┐ │  Bosque de Chapultepec          ← New York 衬线大标题      │
│ │Launch   │ │Yesterday│ │  Often referred to as the "lungs" of the city…            │
│ │Email    │ │History  │ │   • Chapultepec Castle                                    │ ← BulletList
│ │Here are…│ │of Motion│ │   • National Museum of Anthropology                       │
│ └─────────┘ │Pictures │ │   • Chapultepec Zoo                                        │
│ ┌─────────┐ │ ┌─────┐ │ │  ┌──────────┐                                             │
│ │ 8:14 AM │ │ │ b&w │ │ │  │Wikipedia+2│  ← SourceChips (可展开引用)                 │
│ │🍄 thumb │ │ └─────┘ │ │  └──────────┘                                             │
│ │Chanter… │ └─────────┘ │  ┌─ 👍 👎 ─ 60s undo · ✎reason ─┐  ← RatingWidget          │ ←GET /invocations/recent
│ └─────────┘             │  └──────────────────────────────┘    POST /feedback        │
│  ⋮ (scroll)             │                                                            │
│                         │   ╭─────────────────────────────────────────────────╮     │
│                         │   │ ［＋］  Ask Siri…                          ［🎙️］ │‹glass›│ ← AskSiriInputBar
│                         │   ╰─────────────────────────────────────────────────╯     │   (浮起玻璃 pill)
╰─────────────────────────┴──────────────────────────────────────────────────────────╯   ←prompt.submit
   180–400pt (ideal 220)       minWidth 400pt
```

数据流：卡片 ←`session.list`/`session.history`；transcript ←`session.resume` 历史回放 + `message.*` 流式；输入 →`prompt.submit`。

---

## W2 · 侧栏卡片网格（ConversationGallery）细节

双列 masonry：维护 `leftCol`/`rightCol` 累计高度，新卡片贪心放进更矮的那列。卡片高度随内容（有无缩略图、摘要行数）变化。

```
‹GlassEffectContainer 整组玻璃›
 col-A (左, 累计高 h_A)        col-B (右, 累计高 h_B)        gutter 12pt
┌─────────────────────────┐   ┌─────────────────────────┐
│ 📌 Monday        ⋯       │   │ 9:41 AM          ⋯      │  ← 顶: 时间戳 + 置顶📌 + hover⋯菜单
│ Healthy 30-Minute       │   │ Mexico City             │  ← 标题 (SF Pro Semibold, ≤2行)
│ Recipes                 │   │ Largest Park            │
│ You can prepare a       │   │ ┌─────────────────────┐ │  ← 可选缩略图 (AsyncImage, 圆角10)
│ variety of healthy and  │   │ │   [ thumbnail ]     │ │
│ satisfying meals…       │   │ └─────────────────────┘ │
│ ░ 浅玻璃卡 · 圆角14 ░    │   └─────────────────────────┘
└─────────────────────────┘   ┌─────────────────────────┐
┌─────────────────────────┐   │ Yesterday        ⋯      │
│ 9:27 AM          ⋯      │   │ History of Motion       │
│ Social Media Launch     │   │ Pictures                │
│ Email                   │   │ ┌─────────────────────┐ │
│ Here are a few ways…    │   │ │  [ b&w thumbnail ]  │ │
└─────────────────────────┘   │ └─────────────────────┘ │
   selected → accent 描边         └─────────────────────────┘
   + 轻微 scale 1.02
```

交互：单击 →`session.resume`；右键 ⋯ → 置顶 / 重命名(`session.title`) / 删除(`session.delete`) / 分支(`session.branch`)。
卡片态：default(浅玻璃) / hover(描边+⋯显现) / selected(accent 描边+放大)。

---

## W3 · 助手回复解剖（AssistantResponse 渲染管线）

左对齐、无气泡，富文本分块流式渲染。Markdown → AttributedString（借 macai `MessageParser`）。

```
AssistantResponse (VStack, 左对齐, 无底色)
│
├─ TextBlock        正文段落          SF Pro .body          ←message.delta 流式追加
│
├─ InlineImageCard  ┌──────────────────────────┐
│                   │  圆角12 · 轻投影 · 横幅    │           QuickLook 放大 (borrow ZoomableImageView)
│                   └──────────────────────────┘
│
├─ Heading          Bosque de Chapultepec        New York 衬线 .title
│
├─ TextBlock        正文…
│
├─ BulletList       • item                       SF Pro · 圆点缩进
│                   • item
│
├─ CodeBlock(可选)  ┌──────────────────────────┐  HighlightedText (borrow macai) · 等宽 · 玻璃底
│                   │ ```lang … ```            │
│                   └──────────────────────────┘
│
├─ SourceChips      [Wikipedia][+2]              玻璃胶囊 · 点击展开引用列表
│
└─ MetaRow(淡)      ⏱2.1s · 1.2k tok · model     ←message.complete.usage
```

流式时序：`message.start`(建空块) → `message.delta×N`(逐字/逐块追加, UI 合批 16ms) → `message.complete`(替换为最终全文 + usage + chips)。

---

## W4 · Agent 活动可视化（ToolActivity / Reasoning）

Echo 是 Agent（会调工具、有 reasoning），比纯聊天多一层过程展示。用更淡的玻璃 + 单色图标，不抢最终回复的视觉重量。

```
顶部状态条 (GlassToolbar 下方细条)
┌────────────────────────────────────────────────────────────┐
│ ◐ Thinking…                                                  │ ←status.update{kind,text}
└────────────────────────────────────────────────────────────┘

进行中：
┌─ 🔧 Running  read_file ──────────────────────────────┐
│   preview: opening plugins/echo_signals/signals.py…  │ ←tool.generating{name} / tool.progress{name,preview}
└──────────────────────────────────────────────────────┘

完成后折叠：
┌─ ✓ read_file · 0.4s ─────────────────────────  ［展开▾］┐ ←tool.complete{name,summary,duration_s,error?}
└──────────────────────────────────────────────────────────┘
   error 时: ✗ 红色 + error 文案

思考过程（折叠块, 借 macai ThinkingProcessView）：
┌─ 💭 Reasoning ───────────────────────────────  ［展开▾］┐ ←reasoning.delta / thinking.delta (累积)
│ (展开后显示累积的推理文本, 次级灰字)                      │   reasoning.available → 标记本轮有
└──────────────────────────────────────────────────────────┘
```

---

## W5 · Echo 信号卡（核心价值的原生化）

### W5a · RatingWidget（每轮回复下方）
```
idle:           ┌─────────────────────────────────┐
                │  这条有用吗?   ［👍］  ［👎］      │ ←GET /invocations/recent?session_id=
                └─────────────────────────────────┘
rated (60s 窗): ┌─────────────────────────────────────────────────┐
                │  ✓ 已记录 👍   ［撤销］  ［✎ 补充理由］   ⏳60s   │ →POST /feedback{invocation_id,rating}
                └─────────────────────────────────────────────────┘
reason:         ┌─────────────────────────────────────────────────┐
                │  ┌───────────────────────────────────────────┐  │
                │  │ 说说哪里好/不好…(LLM 会按你的话校准置信度)  │  │ →POST /feedback{…,reason}
                │  └───────────────────────────────────────────┘  │   (reason_scorer 打分)
                │                              ［取消］ ［提交］     │
                └─────────────────────────────────────────────────┘
```
状态机：idle →(点👍/👎) rated(60s 倒计时) →(展开) reason；倒计时到 → 提交；撤销 → 回 idle。队列：本会话未评分的 invocation 依次出现，评完淡出。

### W5b · ScopeQuestionCard（M2 · 技能新建后）
```
┌──────────────────────────────────────────────────────────────┐
│  刚才这套做法要怎么复用?            ←GET /scope/pending?session_id │
│  ┌────────────────────────┐  ┌────────────────────────────┐    │
│  │ A · 复用整套方法        │  │ B · 只复用大致想法           │    │ →POST /scope{skill_id,level,session_id}
│  │   (reuse the approach)  │  │   (the general idea)         │    │
│  └────────────────────────┘  └────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
   优先级高于 RatingWidget；本会话已答用本地 set 防闪烁
```

### W5c · ClarifyCard（M1 主动提名 · clarify 往返）★关键链路
```
┌──────────────────────────────────────────────────────────────┐
│  💡  你这套「批量重命名+提交」的做法,要不要存成一个技能?      │ ←event clarify.request
│      {question}                                                │   {question, choices, request_id}
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ 好,存为技能   │  │ 不用了        │  │ 这次不要      │          │ →call clarify.respond
│  │  {choices[0]} │  │  {choices[1]} │  │  {choices[2]} │          │   {session_id,request_id,answer}
│  └──────────────┘  └──────────────┘  └──────────────┘          │
└──────────────────────────────────────────────────────────────┘
   (agent 据答案决定是否 skill_manage create) — Step 25 的「full round-trip」由本 App 走通
```

---

## W6 · Echo 侧面板（可选 · EchoSidePanel）

`HSplitView` 右侧再分一栏（或 inspector 抽屉），复用 dashboard REST。MVP 可不做（决策 §14.5）。

```
┌─ ECHO ─────────────────────────────┐
│ ░ StatusStrip ░                     │ ←GET /status (schema v8 · 编码器 neural · 行数)
│ ──────────────────────────────────  │
│ ▾ 置信度排名 (worst-first)          │ ←GET /skills
│   ▆▆▁ ascii-art        0.42 ⚠       │
│   ▆▆▆ rename-batch     0.71         │
│   ▆▆▆ marketing-email  0.88         │
│ ──────────────────────────────────  │
│ ▾ 新技能候选 (M1)                    │ ←GET /candidates · /candidates/sessions
│   #142  score 130  [save·recur]     │
│   #138  score 60   [tool≥5]         │
│ ──────────────────────────────────  │
│ ▾ 偏好库 (M5)                        │ ←GET /preferences  · DELETE /preferences/{id}
│   "微服务架构图" ×3   0.91   [🗑]    │
│   …                                  │
└──────────────────────────────────────┘
```

---

## W7 · 空态 / Welcome（无选中会话）

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                   │
│                        ◇  Echo                                    │  ← logo / 粒子 (可选保留 macai SceneKit)
│                                                                   │
│                  开始一段对话, 或从左侧打开历史                     │
│                                                                   │
│                  ┌─────────────────────────────────┐              │
│                  │ ［＋］ Ask Siri…          ［🎙️］  │              │ ←prompt.submit (建会话→发首条)
│                  └─────────────────────────────────┘              │
│                                                                   │
│   提示: ⌘N 新会话 · ⌘F 搜索 · 右键卡片可置顶/分支                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## W8 · 降级对照（macOS 26 ↔ 14/15，依决策 §14.1）

```
            macOS 26+ (Liquid Glass)          macOS 14/15 (fallback)
窗口背景    backgroundExtensionEffect          .ultraThinMaterial
按钮        .glassEffect(.regular)             Capsule + .thinMaterial + 描边
卡片        .glassEffect(in: rrect14)          rrect14 + .regularMaterial + shadow
morph 过渡  .glassEffectID + @Namespace        .matchedGeometryEffect (无玻璃融合)
分组        GlassEffectContainer               普通 VStack/ZStack
```
统一封装在 `DesignSystem/GlassStyles.swift` 的 `glassCard()` / `GlassButtonStyle`，一处 `#available(macOS 26,*)` 切换，View 代码不散落可用性判断。

---

## 尺寸速查

| 元素 | 值 |
|---|---|
| 窗口圆角 / 卡片圆角 / 按钮圆角 | 18 / 14 / 10 |
| 侧栏宽 | min 180 · ideal 220 · max 400 |
| 主区 minWidth | 400 |
| 卡片 gutter / 内 padding | 12 / 10 |
| 内容 padding | 16 |
| 输入条圆角 | Capsule(∞) |
| 流式合批间隔 | 16ms |
| 评分撤销窗 | 60s |

> 字体/色板/材质 token 见 [siri-app-ui-plan.md §5](siri-app-ui-plan.md)。
