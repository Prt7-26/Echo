# EchoSiri — 原生 macOS 桌面应用

外观复刻 macOS 新 Siri App（Liquid Glass），后端复用 Echo 的 Agent Harness
（`tui_gateway` JSON-RPC over WebSocket + `echo_signals` REST）。

设计与计划：
- [../DevPlan/siri-app-ui-plan.md](../DevPlan/siri-app-ui-plan.md) — UI 层
- [../DevPlan/desktop-app-dev-plan.md](../DevPlan/desktop-app-dev-plan.md) — 工程实施手册（协议契约/WBS）
- [../DevPlan/siri-app-wireframes.md](../DevPlan/siri-app-wireframes.md) — 线框图

## 构建

主构建系统是 **Swift Package Manager**（CLT 即可，无需完整 Xcode）：

```bash
cd desktop/EchoSiri
swift build            # 编译
swift test             # 跑 EchoSiriKit 单测（协议层/模型/Store）
swift run EchoSiri     # 启动 App（需 GUI 会话）
```

- 工具链：Swift 6.3+，macOS 26 SDK（Liquid Glass `.glassEffect` 真 API）。
- 也可直接用 Xcode 26 打开 `Package.swift` 做签名/打包/可视化预览。

## 目标系统

- **主打 macOS 26 Tahoe+**：真 Liquid Glass。
- **基线降级 macOS 15**：`DesignSystem/GlassStyles.swift` 用 `.regularMaterial` 等效降级，
  唯一一处 `#available(macOS 26, *)` 切换，View 代码不散落可用性判断。

## 目标结构（建设中）

```
Sources/
├─ EchoSiri/        可执行 App：App / DesignSystem / Views / RichText
└─ EchoSiriKit/     纯逻辑：Models(Codable) / Services(Gateway/Echo client) / Stores
Tests/
└─ EchoSiriKitTests/  协议层 + 模型解码 + Store 逻辑单测
```

进度见 [../CLAUDE.md](../CLAUDE.md) 与 DevPlan 的 WBS。
