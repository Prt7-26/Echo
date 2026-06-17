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
                // HIG: sidebar 是导航层，由 NavigationSplitView 自动套用系统 sidebar 材质；
                // 不再自铺材质（避免双层材质/玻璃叠玻璃）。
        } detail: {
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
            // 窗口要透明才能让 sidebar 透出桌面 → 右侧内容列必须自带实底兜底。
            .background(Theme.contentBackground)
        }
        .frame(minWidth: Tokens.Size.windowMinWidth, minHeight: Tokens.Size.windowMinHeight)
        // 不再手动改窗口透明度（那会卡、还破坏调度中心）。交给 SwiftUI 原生：
        // NavigationSplitView 的 sidebar 自带系统半透材质透出桌面，且只透 sidebar 区、
        // 不碰整窗合成——既是 WeChat/Siri 的正确结构，又是正常受管理窗口（调度中心正常）。
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
