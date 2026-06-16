import SwiftUI
import EchoSiriKit

@main
struct EchoSiriApp: App {
    @State private var app = AppState.mock()   // Phase 3: 改为接 GatewayClient

    var body: some Scene {
        WindowGroup {
            RootSplitView(app: app)
                .containerBackground(.ultraThinMaterial, for: .window)
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands { ConversationCommands(app: app) }
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
