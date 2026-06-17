import SwiftUI

/// 对话主区（线框图 W1 右栏）：顶栏 + 状态条 + transcript 滚动 + Echo 信号 + 输入条。
struct ConversationPane: View {
    @Bindable var app: AppState

    var body: some View {
        // transcript 充满全区，内容滚到顶栏「下方」（safeAreaInset），上缘由 scrim 渐隐。
        TranscriptScroll(app: app)
            .safeAreaInset(edge: .top, spacing: 0) {
                VStack(spacing: 0) {
                    ConversationTopBar(app: app)
                    if app.connection != .online {
                        ConnectionBanner(state: app.connection)
                    }
                    if let status = app.statusLine {
                        StatusStrip(text: status)
                    }
                }
                .offset(y: -Tokens.topBarRaise)   // 上抬与红绿灯齐平
                .topBarScrim()
            }
            // 忽略顶部安全区 → 顶栏（标题+搜索/信号/溢出）升到标题栏区、与 traffic-light 齐平。
            .ignoresSafeArea(.container, edges: .top)
            .overlay(alignment: .bottom) {
                // 浮起输入条 + 其上方的 Echo 信号卡
                VStack(spacing: 8) {
                    EchoSignalOverlay(app: app)
                    ComposerBar(app: app)
                }
            }
            .background(Theme.contentBackground.ignoresSafeArea())
    }
}

/// 对话区顶栏（搜索 / 标题 / 溢出菜单）。
struct ConversationTopBar: View {
    @Bindable var app: AppState

    var body: some View {
        HStack(spacing: 10) {
            Text(app.selectedConversation?.title ?? "New Conversation")
                .font(Tokens.Typeface.navTitle)
                .lineLimit(1)
            Spacer()
            Button {} label: { Image(systemName: "magnifyingglass") }
                .buttonStyle(.glassIcon)
            Button { app.toggleEchoPanel() } label: {
                Image(systemName: app.showEchoPanel ? "sidebar.right" : Theme.logoSymbol)
            }
            .buttonStyle(.glassIcon)
            .help("Echo 面板")
            Menu {
                Button("重命名…") {}
                Button("压缩上下文") {}
                Button("导出…") {}
            } label: { Image(systemName: "ellipsis") }
                .menuStyle(.button)
                .buttonStyle(.glassIcon)
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
            Text(text).font(Tokens.Typeface.meta)
            Spacer()
        }
        .foregroundStyle(Theme.secondaryText)
        .padding(.horizontal, Tokens.Spacing.content)
        .padding(.vertical, 5)
    }
}

/// 连接状态细横幅（offline/connecting 时显示；online 不显示）。
/// 后端冷启动（重型 import 可达数十秒）或断线重连期间给用户明确反馈。
struct ConnectionBanner: View {
    let state: AppState.Connection
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: state == .connecting ? "circle.dotted" : "wifi.slash")
                .symbolEffect(.pulse, isActive: state == .connecting)
            Text(state == .connecting ? "正在连接 Echo 后端…" : "后端未连接")
                .font(Tokens.Typeface.meta)
            Spacer()
        }
        .foregroundStyle(state == .connecting ? Theme.accent : Theme.Signal.negative)
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
            // 流式增长时跟随到底部：transcript 条数不变，靠 streamTick 触发（即时、不加动画，避免逐刷排队）。
            .onChange(of: app.streamTick) {
                proxy.scrollTo("bottom-spacer", anchor: .bottom)
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
