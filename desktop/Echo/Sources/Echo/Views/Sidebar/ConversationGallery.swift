import SwiftUI

/// 侧栏会话画廊（线框图 W2）：顶部工具栏 + 双列错落卡片滚动区。
struct ConversationGallery: View {
    @Bindable var app: AppState

    var body: some View {
        // 卡片滚到工具栏「下方」（safeAreaInset），上缘由 scrim 渐隐。
        ScrollView {
            MasonryLayout(columns: 2, spacing: Tokens.Spacing.cardGutter) {
                ForEach(sortedConversations) { conv in
                    ConversationCard(summary: conv,
                                     isSelected: conv.id == app.selectedConversationId)
                        .onTapGesture { app.selectConversation(conv.id) }
                        .contextMenu { menu(for: conv) }
                }
            }
            .padding(.horizontal, Tokens.Spacing.cardPadding)
            .padding(.vertical, Tokens.Spacing.content)
        }
        .scrollContentBackground(.hidden)   // ScrollView 透明
        .frame(maxHeight: .infinity)
        .safeAreaInset(edge: .top, spacing: 0) {
            SidebarToolbar(app: app)
        }
        // 忽略顶部安全区 → 顶栏升到标题栏区、与 traffic-light 齐平（按钮已右对齐，不挡红绿灯）。
        .ignoresSafeArea(.container, edges: .top)
        // 侧栏透明（背景由 SidebarVibrancyController 的 NSVisualEffectView 透出桌面）；
        // 卡片是实底 contentCard 浮在其上。
    }

    /// 置顶优先，其余按时间倒序。
    private var sortedConversations: [ConversationSummary] {
        app.conversations.sorted {
            if $0.pinned != $1.pinned { return $0.pinned && !$1.pinned }
            return $0.timestamp > $1.timestamp
        }
    }

    @ViewBuilder
    private func menu(for conv: ConversationSummary) -> some View {
        Button(conv.pinned ? "取消置顶" : "置顶") { app.togglePin(conv.id) }
        Button("重命名…") { /* Phase 3: gateway.sessionTitle */ }
        Button("分支…") { /* Phase 3: gateway.sessionBranch */ }
        Divider()
        Button("删除", role: .destructive) { app.deleteConversation(conv.id) }
    }
}

#if canImport(PreviewsMacros)
#Preview("Gallery") {
    ConversationGallery(app: .mock())
        .frame(width: 240, height: 600)
}
#endif
