import SwiftUI

/// 根三栏（线框图 W1）：左侧画廊 + 右侧对话区/空态。
/// 分栏用 AppKit `NSSplitViewController`（真系统 sidebar，可控的 NSVisualEffectView），
/// 而非 SwiftUI NavigationSplitView——后者的侧栏半透是内部实现，控不了边框/失焦状态。
struct RootSplitView: View {
    @Bindable var app: AppState

    var body: some View {
        AppKitSplitView(app: app)
            .frame(minWidth: Tokens.Size.windowMinWidth, minHeight: Tokens.Size.windowMinHeight)
            .ignoresSafeArea()
    }
}

/// detail 列：对话区/空态 + 可选 Echo 侧面板。实底（内容层不透明）。
struct DetailContainer: View {
    @Bindable var app: AppState

    var body: some View {
        HStack(spacing: 0) {
            Group {
                if app.selectedConversationId != nil {
                    ConversationPane(app: app)
                } else {
                    WelcomeScreen(app: app)
                }
            }
            .frame(minWidth: Tokens.Size.detailMin)

            if app.showEchoPanel {
                Divider()
                EchoSidePanel(app: app)
                    .transition(.move(edge: .trailing))
            }
        }
        .animation(.smooth(duration: 0.2), value: app.showEchoPanel)
        .background(Theme.contentBackground)   // 内容层实底
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
