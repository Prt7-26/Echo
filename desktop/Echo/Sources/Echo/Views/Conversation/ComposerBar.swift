import SwiftUI

/// 底部浮起玻璃输入条（线框图 W1/W5）：＋ / 多行输入 / 🎙️(推理中变 ⏹)。
struct ComposerBar: View {
    @Bindable var app: AppState
    @FocusState private var focused: Bool

    var body: some View {
        // 三个独立玻璃组件：+（圆）/ 文本框（长胶囊）/ 麦克风（圆）——
        // 各自独立的 Liquid Glass 包围，符合 Apple 设计规范。
        HStack(spacing: 10) {
            Button { /* Phase 4: 附件/动作菜单 */ } label: {
                Image(systemName: "plus")
            }
            .buttonStyle(.glassIcon)   // 独立圆形玻璃

            TextField("Ask Echo…", text: $app.composerText, axis: .vertical)
                .textFieldStyle(.plain)
                .font(Tokens.Typeface.body)
                .lineLimit(1...6)
                .focused($focused)
                .onSubmit(submit)
                .padding(.horizontal, 18)
                .frame(minHeight: GlassIcon.diameter)
                .glassPanel(cornerRadius: GlassIcon.diameter / 2)   // 独立长胶囊玻璃

            Button(action: trailingAction) {
                Image(systemName: app.isResponding ? "stop.fill" : "mic.fill")
                    .foregroundStyle(app.isResponding ? Theme.Signal.negative : Theme.accent)
            }
            .buttonStyle(.glassIcon)   // 独立圆形玻璃
        }
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
    ComposerBar(app: .mock())
        .frame(width: 640)
        .padding()
}
#endif
