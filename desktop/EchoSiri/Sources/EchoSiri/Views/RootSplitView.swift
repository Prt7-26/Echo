import SwiftUI

/// 根三栏（线框图 W1）：左侧画廊 + 右侧对话区/空态。
struct RootSplitView: View {
    @Bindable var app: AppState
    @State private var columnVisibility = NavigationSplitViewVisibility.all

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            ConversationGallery(app: app)
                .navigationSplitViewColumnWidth(
                    min: Tokens.Size.sidebarMin,
                    ideal: Tokens.Size.sidebarIdeal,
                    max: Tokens.Size.sidebarMax
                )
                .background(WindowGlassBackground().ignoresSafeArea())
        } detail: {
            if app.selectedConversationId != nil {
                ConversationPane(app: app)
                    .frame(minWidth: Tokens.Size.detailMin)
            } else {
                WelcomeScreen(app: app)
                    .frame(minWidth: Tokens.Size.detailMin)
            }
        }
        .frame(minWidth: Tokens.Size.windowMinWidth, minHeight: Tokens.Size.windowMinHeight)
    }
}

#if canImport(PreviewsMacros)
#Preview("Root · selected") {
    RootSplitView(app: .mock())
        .frame(width: 1100, height: 720)
}
#Preview("Root · welcome") {
    RootSplitView(app: .mock(selected: false))
        .frame(width: 1100, height: 720)
}
#endif
