import SwiftUI
import AppKit
import EchoKit

@main
struct EchoApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var app = AppState.mock()   // ECHO_APP_CONNECT=1 时切真后端

    var body: some Scene {
        WindowGroup {
            RootSplitView(app: app)
                // 不再给整窗铺毛玻璃：大面积 backdrop-blur 会逼 WindowServer 每帧
                // 重合成全屏、拖累 ProMotion 与同屏其它窗口。内容区用实底，sidebar
                // 由系统给材质，窗口本身保持不透明即可（HIG：内容层不用玻璃）。
                .task {
                    // 设 ECHO_APP_CONNECT=1 接真后端；否则保留 mock 数据走查。
                    if ProcessInfo.processInfo.environment["ECHO_APP_CONNECT"] == "1" {
                        app.connectLive()
                    }
                }
        }
        .windowStyle(.hiddenTitleBar)
        // 不用 .unified 工具栏：我们没有 toolbar item（按钮都在 SwiftUI 内容里），
        // 而 unified 样式会在标题栏区画一条深色统一工具栏背景 → 盖住玻璃背板 = 顶部黑条。
        .commands { ConversationCommands(app: app) }
    }
}

/// 经 `swift run` 启动的 SPM 可执行程序没有 .app bundle/Info.plist，默认不会
/// 前台化、无 dock 图标。在此把激活策略设为 .regular 并激活，确保窗口弹出。
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        // swift-run 没有 .icns bundle → dock 是通用图标。用 Echo 波形标记现绘一个
        // dock 图标（与界面 EchoLogo 同源符号）。打分发版时再换成真正的 AppIcon.icns。
        if let icon = AppDelegate.makeDockIcon() { NSApp.applicationIconImage = icon }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    /// 现绘 dock 图标：圆角浅底 + sonar-teal 波形标记（Theme.logoSymbol 同源）。
    static func makeDockIcon() -> NSImage? {
        let teal = NSColor(red: 0.10, green: 0.66, blue: 0.62, alpha: 1)
        let side: CGFloat = 512
        let img = NSImage(size: NSSize(width: side, height: side))
        img.lockFocus()
        defer { img.unlockFocus() }

        // 圆角底板（macOS 圆角矩形比例 ≈ 0.225 * 边长）。
        let inset: CGFloat = 36
        let plate = NSRect(x: inset, y: inset, width: side - inset * 2, height: side - inset * 2)
        let radius = plate.width * 0.225
        let bg = NSBezierPath(roundedRect: plate, xRadius: radius, yRadius: radius)
        NSColor(calibratedWhite: 0.98, alpha: 1).setFill()
        bg.fill()

        // 波形标记，teal 着色，居中。
        let cfg = NSImage.SymbolConfiguration(pointSize: 256, weight: .regular)
            .applying(.init(paletteColors: [teal]))
        if let sym = NSImage(systemSymbolName: "waveform.path.ecg", accessibilityDescription: "Echo")?
            .withSymbolConfiguration(cfg) {
            let s = sym.size
            let r = NSRect(x: (side - s.width) / 2, y: (side - s.height) / 2, width: s.width, height: s.height)
            sym.draw(in: r)
        }
        return img
    }
}

/// 菜单栏 Conversation 菜单（线框图 W1 菜单栏）。
struct ConversationCommands: Commands {
    @Bindable var app: AppState

    var body: some Commands {
        CommandMenu("Conversation") {
            Button("New Conversation") { app.newConversation() }
                .keyboardShortcut("n", modifiers: .command)
            Divider()
            Button("Pin / Unpin") {
                if let id = app.selectedConversationId { app.togglePin(id) }
            }
            Button("Delete", role: .destructive) {
                if let id = app.selectedConversationId { app.deleteConversation(id) }
            }
            .keyboardShortcut(.delete, modifiers: .command)
        }
    }
}
