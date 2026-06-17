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
        .scrollContentBackground(.hidden)   // 关键：让 ScrollView 透明，露出原生 sidebar 半透材质
        .frame(maxHeight: .infinity)
        .safeAreaInset(edge: .top, spacing: 0) {
            SidebarToolbar(app: app).topBarScrim()
        }
        // 不自铺任何 sidebar 大底。用 NavigationSplitView 的原生 sidebar 材质
        // （系统 .behindWindow 半透玻璃，透出桌面）——这正是 WeChat/Finder/Siri 的
        // 「透明大底 + 不透明内容层（卡片）」结构，且系统原生只让 sidebar 那块透、
        // 不碰整窗合成，所以不会卡。卡片是实底 contentCard，盖在透明大底之上。
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
