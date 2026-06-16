import SwiftUI
import AppKit
import EchoSiriKit

@main
struct EchoSiriApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var app = AppState.mock()   // ECHOSIRI_CONNECT=1 时切真后端

    var body: some Scene {
        WindowGroup {
            RootSplitView(app: app)
                .containerBackground(.ultraThinMaterial, for: .window)
                .task {
                    // 设 ECHOSIRI_CONNECT=1 接真后端；否则保留 mock 数据走查。
                    if ProcessInfo.processInfo.environment["ECHOSIRI_CONNECT"] == "1" {
                        app.connectLive()
                    }
                }
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands { ConversationCommands(app: app) }
    }
}

/// 经 `swift run` 启动的 SPM 可执行程序没有 .app bundle/Info.plist，默认不会
/// 前台化、无 dock 图标。在此把激活策略设为 .regular 并激活，确保窗口弹出。
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
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
