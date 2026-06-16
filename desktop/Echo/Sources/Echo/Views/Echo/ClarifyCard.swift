import SwiftUI

/// M1 主动提名 clarify 卡（线框图 W5c）★ Echo 关键链路。
/// gateway clarify.request → 渲染此卡 → 用户选 → clarify.respond{answer}。
struct ClarifyCard: View {
    let prompt: ClarifyPrompt
    /// 回传所选答案（answer 文本）。
    let onAnswer: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "lightbulb.fill")
                    .foregroundStyle(Theme.Signal.nomination)
                Text(prompt.question)
                    .font(Tokens.Typeface.callout).foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 10) {
                ForEach(prompt.choices, id: \.self) { choice in
                    Button { onAnswer(choice) } label: {
                        Text(choice).font(Tokens.Typeface.callout).padding(.horizontal, 4).padding(.vertical, 2)
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.glassIcon)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard(cornerRadius: Tokens.Radius.card, tinted: true)
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2)
                .fill(Theme.Signal.nomination)
                .frame(width: 3)
                .padding(.vertical, 6)
        }
    }
}

#if canImport(PreviewsMacros)
#Preview {
    ClarifyCard(prompt: MockData.sampleClarify) { _ in }
        .frame(width: 580).padding()
}
#endif
