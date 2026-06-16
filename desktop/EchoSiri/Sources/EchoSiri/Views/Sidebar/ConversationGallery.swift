import SwiftUI

/// 侧栏会话画廊（线框图 W2）：顶部工具栏 + 双列错落卡片滚动区。
struct ConversationGallery: View {
    @Bindable var app: AppState

    var body: some View {
        VStack(spacing: 0) {
            SidebarToolbar(app: app)
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
        }
        .frame(maxHeight: .infinity)
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
