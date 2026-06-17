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
                // ⏎ 发送，⇧⏎ 换行（多行框默认回车=换行、根本发不出去 → 这里拦截）。
                .onKeyPress(keys: [.return]) { press in
                    if press.modifiers.contains(.shift) { return .ignored }  // ⇧⏎ → 换行
                    submit(); return .handled                                // ⏎ → 发送
                }
                .padding(.horizontal, 18)
                .frame(minHeight: GlassIcon.diameter)
                .glassPanel(cornerRadius: GlassIcon.diameter / 2)   // 独立长胶囊玻璃

            // 有文字 → 发送(↑)；推理中 → 停止(⏹)；否则 → 麦克风。
            // （之前只有麦克风空桩 → 根本没法发送。）
            Button(action: trailingAction) {
                Image(systemName: trailingSymbol)
                    .foregroundStyle(trailingTint)
            }
            .buttonStyle(.glassIcon)   // 独立圆形玻璃
            .disabled(!app.isResponding && !hasText)    // 空且非推理：麦克风(暂空操作)
        }
        .padding(.horizontal, Tokens.Spacing.loose)
        .padding(.bottom, Tokens.Spacing.content)
    }

    private var hasText: Bool {
        !app.composerText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    private var trailingSymbol: String {
        if app.isResponding { return "stop.fill" }
        return hasText ? "arrow.up" : "mic.fill"
    }
    private var trailingTint: Color {
        app.isResponding ? Theme.Signal.negative : Theme.accent
    }

    private func submit() { if hasText { app.send() } }
    private func trailingAction() {
        if app.isResponding { app.stop() }
        else if hasText { app.send() }
        // else: 麦克风（Phase 2 voice.toggle，暂空）
    }
}

#if canImport(PreviewsMacros)
#Preview {
    ComposerBar(app: .mock())
        .frame(width: 640)
        .padding()
}
#endif
