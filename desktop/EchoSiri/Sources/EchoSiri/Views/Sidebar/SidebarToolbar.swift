import SwiftUI

/// 侧栏顶部工具栏（线框图 W1 左上）：筛选 + 新建。
struct SidebarToolbar: View {
    @Bindable var app: AppState

    var body: some View {
        HStack(spacing: Tokens.Spacing.tight) {
            Menu {
                Button("全部") {}
                Button("仅置顶") {}
                Button("按时间") {}
            } label: {
                Image(systemName: "line.3.horizontal.decrease")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()

            Button { app.newConversation() } label: {
                Image(systemName: "square.and.pencil")
            }
            .buttonStyle(.glassIcon)
            .help("新建对话")

            Spacer()
        }
        .font(.system(size: 13, weight: .medium))
        .foregroundStyle(.secondary)
        .padding(.horizontal, Tokens.Spacing.content)
        .padding(.vertical, Tokens.Spacing.cardPadding)
    }
}

#if canImport(PreviewsMacros)
#Preview {
    SidebarToolbar(app: .mock()).frame(width: 240)
}
#endif
