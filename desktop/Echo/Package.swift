// swift-tools-version: 6.0
import PackageDescription

// 注意：本机只有 Command Line Tools（无完整 Xcode），所以 XCTest / swift-testing
// 模块不可用，`swift test` 跑不了。为了在 CLT 下仍能持续验证协议层逻辑，
// 用一个可执行的自检 target `echo-check`（断言 + 非零退出码）：
//     swift run echo-check
// 等维护者装了 Xcode 26，可把 Tests/ 下的 XCTest 版本接回 `swift test`。

let package = Package(
    name: "Echo",
    platforms: [
        .macOS(.v15) // 主打 macOS 26 (Liquid Glass), 基线降级到 15
    ],
    products: [
        .executable(name: "Echo", targets: ["Echo"]),
        .library(name: "EchoKit", targets: ["EchoKit"]),
    ],
    targets: [
        // 纯逻辑 + 协议层 (Codable / GatewayClient / EchoAPIClient / Stores)，可脱离 UI 验证
        .target(
            name: "EchoKit",
            path: "Sources/EchoKit"
        ),
        // 可执行 App: @main + DesignSystem + Views，依赖 Kit
        .executableTarget(
            name: "Echo",
            dependencies: ["EchoKit"],
            path: "Sources/Echo"
        ),
        // CLT 下可跑的自检 harness（替代 swift test）
        .executableTarget(
            name: "echo-check",
            dependencies: ["EchoKit"],
            path: "Sources/EchoCheck"
        ),
    ]
)
