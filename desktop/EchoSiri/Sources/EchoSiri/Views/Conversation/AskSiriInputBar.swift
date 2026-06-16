import SwiftUI

/// 底部浮起玻璃输入条（线框图 W1/W5）：＋ / 多行输入 / 🎙️(推理中变 ⏹)。
struct AskSiriInputBar: View {
    @Bindable var app: AppState
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 10) {
            Button { /* Phase 4: 附件/动作菜单 */ } label: {
                Image(systemName: "plus")
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 26, height: 26)
            }
            .buttonStyle(.glassIcon)

            TextField("Ask Siri…", text: $app.composerText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(Tokens.Typeface.body)
                .lineLimit(1...6)
                .focused($focused)
                .onSubmit(submit)

            Button(action: trailingAction) {
                Image(systemName: app.isResponding ? "stop.fill" : "mic.fill")
                    .font(.system(size: 15, weight: .medium))
                    .frame(width: 26, height: 26)
                    .foregroundStyle(app.isResponding ? Theme.Signal.negative : Theme.accent)
            }
            .buttonStyle(.glassIcon)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .glassPanel(cornerRadius: 24)
        .padding(.horizontal, Tokens.Spacing.loose)
        .padding(.bottom, Tokens.Spacing.content)
    }

    private func submit() {
        // Enter 发送；Shift+Enter 由 axis:.vertical 的换行处理
        app.send()
    }
    private func trailingAction() {
        if app.isResponding { app.stop() } else { /* Phase 2: voice.toggle */ }
    }
}

#if canImport(PreviewsMacros)
#Preview {
    AskSiriInputBar(app: .mock())
        .frame(width: 640)
        .padding()
}
#endif
