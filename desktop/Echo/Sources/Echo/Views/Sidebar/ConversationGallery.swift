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
        .scrollContentBackground(.hidden)   // 别让 ScrollView 铺实底盖住下面的材质
        .frame(maxHeight: .infinity)
        .safeAreaInset(edge: .top, spacing: 0) {
            SidebarToolbar(app: app).topBarScrim()
        }
        // 更透：用 .underWindowBackground（标准材质里最见底的一档）盖掉系统 .sidebar 材质，
        // 自己掌控透明度。配合 WindowVibrancyConfigurator 的透明窗口，壁纸大面积透出来。
        // ECHO_NO_VIBRANCY=1 时整块用实底（A/B 排查彩虹球是否源于 vibrancy 合成）。
        .background {
            if ProcessInfo.processInfo.environment["ECHO_NO_VIBRANCY"] != "1" {
                VisualEffectBackground(material: .underWindowBackground).ignoresSafeArea()
            } else {
                Theme.contentBackground.ignoresSafeArea()
            }
        }
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
