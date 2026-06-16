import SwiftUI

/// M2 scope 问题（线框图 W5b）：技能新建后，问复用粒度。
struct ScopeQuestionCard: View {
    let question: ScopeQuestion
    /// 回传选择："specific"(整套) | "general"(大致想法)。
    let onChoose: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("刚才这套做法要怎么复用？")
                .font(Tokens.Typeface.callout).foregroundStyle(.primary)
            HStack(spacing: 10) {
                choice(title: "A · 复用整套方法", subtitle: "reuse the approach", level: "specific")
                choice(title: "B · 只复用大致想法", subtitle: "the general idea", level: "general")
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard(cornerRadius: Tokens.Radius.card, tinted: true)
    }

    private func choice(title: String, subtitle: String, level: String) -> some View {
        Button { onChoose(level) } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.weight(.medium))
                Text(subtitle).font(Tokens.Typeface.metaSmall).foregroundStyle(Theme.secondaryText)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
        }
        .buttonStyle(.glassIcon)
    }
}

#if canImport(PreviewsMacros)
#Preview {
    ScopeQuestionCard(question: MockData.sampleScope) { _ in }
        .frame(width: 560).padding()
}
#endif
