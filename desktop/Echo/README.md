# Echo — 原生 macOS 桌面应用

外观复刻 macOS 新 Siri App（Liquid Glass），后端复用 Echo 的 Agent Harness。
**聊天链路把 `python -m tui_gateway.entry` 作为 stdio 子进程拉起**（与 ui-tui 同构，
零改 Hermes、零 dashboard 依赖）；Echo 信号链路走 dashboard REST。

设计与计划：
- [../DevPlan/siri-app-ui-plan.md](../DevPlan/siri-app-ui-plan.md) — UI 层
- [../DevPlan/desktop-app-dev-plan.md](../DevPlan/desktop-app-dev-plan.md) — 工程实施手册（协议契约/WBS）
- [../DevPlan/siri-app-wireframes.md](../DevPlan/siri-app-wireframes.md) — 线框图

## 运行

主构建系统是 **Swift Package Manager**（CLT 即可，无需完整 Xcode）：

```bash
# 经 ./echo 启动器（推荐）
./echo app                # mock 走查（无后端，看外观/交互）
./echo app --connect      # 接真后端：App 自行 spawn gateway 子进程
./echo app-check          # 跑自检 harness

# 或直接在包内
cd desktop/Echo
swift build               # 编译（含 SwiftUI Views）
swift run echo-check  # 37 项自检（协议/服务/Markdown/退避 + 真管道 mock-gw 流式）
swift run Echo        # 启动 App
```

- 工具链：Swift 6.3+，macOS 26 SDK（真 Liquid Glass `.glassEffect` 已验证可编译）。
- Xcode 26 打开 `Package.swift` 可看 `#Preview`、做签名/打包。

## 验证现状

- ✅ `swift build` 全包编译通过（CLT-only 机器，macOS 26 SDK）。
- ✅ `swift run echo-check` **37/37**：协议 Codable 解码、GatewayClient 请求/响应
  配对 + 事件流、EchoAPIClient URL/body、Markdown 多块解析、指数退避，以及
  **真 stdio 管道端到端**（`scripts/mock_gateway.py`：spawn→ready→list→create→
  prompt 流式）。
- ⚠️ 可视化走查需 Xcode 26（CLT 无 `#Preview` 宏插件，已用 `#if canImport(PreviewsMacros)` 门控）。
- ⚠️ 真后端 live（`ECHO_APP_LIVE=1`）：真 gateway 在本机启动**非确定性**（重型 import +
  更新检查 + 与运行中的 dashboard gateway 争用），故默认不跑；协议/传输已由 mock-gw
  在真管道上确定性验证。

## 结构

```
Sources/
├─ EchoKit/        纯逻辑（可自检）
│  ├─ Protocol/        Codable 模型 + JSON-RPC 信封 + 事件解析器
│  ├─ Services/        GatewayClient(actor) · EchoAPIClient · StdioSubprocessTransport
│  │                   · BackendLocator · ExponentialBackoff
│  └─ RichText/        MarkdownBlocks 解析器
└─ Echo/           可执行 App
   ├─ App/             @main · Conversation 菜单
   ├─ DesignSystem/    Tokens · GlassStyles(26→15 降级) · Theme
   ├─ Models/          UI 视图模型 + MockData
   ├─ Stores/          AppState(@Observable) · GatewayCoordinator · SignalMonitors
   └─ Views/           Sidebar(masonry 画廊) · Conversation(transcript/输入条) ·
                       Echo(Rating/Scope/Clarify 信号卡) · Welcome
scripts/mock_gateway.py   确定性协议替身（自检用）
```

## 已实现（Step 26）

侧栏双列错落卡片网格、对话流（用户 pill + 助手富文本：衬线标题/列表/代码/内联图/
来源 chip + 工具/推理可视化）、浮起玻璃输入条、Echo 信号卡（评分/scope/clarify）、
gateway 事件流→AppState 归约、stdio 子进程接后端、Markdown 渲染、原生剪贴板/焦点信号、
指数退避自动重连、`./echo app` 启动器。

## 待办

- 真机 GUI 跑通（`./echo app --connect`）与可视化打磨。
- 评分 widget 的 60s 撤销 / 补充理由完整 UX（当前即时提交）。
- Echo 侧面板（置信度/候选/偏好）。
- 打包/签名/notarize（需完整 Xcode 26）。
- 6 个待维护者拍板的决策见 dev-plan §14（最低系统、后端自动拉起、Tauri 去留、面板范围…）。
