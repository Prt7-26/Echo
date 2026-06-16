import SwiftUI

/// 空态（线框图 W7）：无选中会话时的引导 + 居中输入条。
struct WelcomeScreen: View {
    @Bindable var app: AppState

    var body: some View {
        ZStack {
            Theme.contentBackground.ignoresSafeArea()
            VStack(spacing: Tokens.Spacing.loose) {
                Spacer()
                Image(systemName: "sparkles")
                    .font(.system(size: 44, weight: .light))
                    .foregroundStyle(Theme.accent)
                Text("Echo")
                    .font(.system(.largeTitle, design: .serif))
                Text("开始一段对话，或从左侧打开历史")
                    .font(.callout)
                    .foregroundStyle(Theme.secondaryText)
                AskSiriInputBar(app: app)
                    .frame(maxWidth: 560)
                Text("提示：⌘N 新对话 · ⌘F 搜索 · 右键卡片可置顶/分支")
                    .font(.caption2)
                    .foregroundStyle(Theme.secondaryText.opacity(0.7))
                Spacer()
            }
            .padding()
        }
    }
}

#if canImport(PreviewsMacros)
#Preview {
    WelcomeScreen(app: .mock(selected: false))
        .frame(width: 760, height: 600)
}
#endif
