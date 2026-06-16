import SwiftUI

/// 对话主区（线框图 W1 右栏）：顶栏 + 状态条 + transcript 滚动 + Echo 信号 + 输入条。
struct ConversationPane: View {
    @Bindable var app: AppState

    var body: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                ConversationTopBar(app: app)
                if let status = app.statusLine {
                    StatusStrip(text: status)
                }
                TranscriptScroll(app: app)
            }
            // 浮起输入条 + 其上方的 Echo 信号卡
            VStack(spacing: 8) {
                EchoSignalOverlay(app: app)
                AskSiriInputBar(app: app)
            }
        }
        .background(WindowGlassBackground().ignoresSafeArea())
    }
}

/// 对话区顶栏（搜索 / 标题 / 溢出菜单）。
struct ConversationTopBar: View {
    @Bindable var app: AppState

    var body: some View {
        HStack(spacing: 10) {
            Text(app.selectedConversation?.title ?? "New Conversation")
                .font(.headline)
                .lineLimit(1)
            Spacer()
            Button {} label: { Image(systemName: "magnifyingglass") }
                .buttonStyle(.glassIcon)
            Button { app.toggleEchoPanel() } label: {
                Image(systemName: app.showEchoPanel ? "sidebar.right" : "waveform.path.ecg")
            }
            .buttonStyle(.glassIcon)
            .help("Echo 面板")
            Menu {
                Button("重命名…") {}
                Button("压缩上下文") {}
                Button("导出…") {}
            } label: { Image(systemName: "ellipsis") }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
        }
        .padding(.horizontal, Tokens.Spacing.content)
        .padding(.vertical, 10)
    }
}

/// 顶部细状态条（Thinking…/Running tool…）。
struct StatusStrip: View {
    let text: String
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "circle.dotted").symbolEffect(.rotate)
            Text(text).font(.caption)
            Spacer()
        }
        .foregroundStyle(Theme.secondaryText)
        .padding(.horizontal, Tokens.Spacing.content)
        .padding(.vertical, 5)
    }
}

/// transcript 滚动区。
struct TranscriptScroll: View {
    @Bindable var app: AppState

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    ForEach(app.transcript) { item in
                        switch item {
                        case .user(let m): UserBubble(message: m).id(item.id)
                        case .assistant(let m): AssistantResponse(message: m).id(item.id)
                        }
                    }
                    // 给浮起输入条 + 信号卡留底部空间
                    Color.clear.frame(height: 120).id("bottom-spacer")
                }
                .padding(.horizontal, Tokens.Spacing.loose)
                .padding(.top, Tokens.Spacing.content)
            }
            .onChange(of: app.transcript.count) {
                withAnimation(.smooth) { proxy.scrollTo("bottom-spacer", anchor: .bottom) }
            }
        }
    }
}

#if canImport(PreviewsMacros)
#Preview("Conversation pane") {
    ConversationPane(app: .mock())
        .frame(width: 720, height: 760)
}
#endif
