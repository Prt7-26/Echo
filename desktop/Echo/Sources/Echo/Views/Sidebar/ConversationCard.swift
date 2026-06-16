import SwiftUI

/// 侧栏一张会话卡（线框图 W2）：时间戳 + 置顶 + 标题 + 摘要 + 可选缩略图。
struct ConversationCard: View {
    let summary: ConversationSummary
    let isSelected: Bool
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: Tokens.Spacing.tight) {
            header
            Text(summary.title)
                .font(Tokens.Typeface.cardTitle)
                .foregroundStyle(.primary)
                .lineLimit(2)
            if let thumb = summary.thumbnailSymbol {
                thumbnail(thumb)
            }
            Text(summary.preview)
                .font(Tokens.Typeface.cardPreview)
                .foregroundStyle(Theme.secondaryText)
                .lineLimit(summary.thumbnailSymbol == nil ? 4 : 2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(Tokens.Spacing.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentCard(cornerRadius: Tokens.Radius.cardLarge, selected: isSelected)  // HIG: 列表项=内容层=实底
        .scaleEffect(isSelected ? 1.015 : 1)
        .animation(.smooth(duration: 0.18), value: isSelected)
        .onHover { hovering = $0 }
        .contentShape(.rect)
    }

    private var header: some View {
        HStack(spacing: 4) {
            if summary.pinned {
                Image(systemName: "pin.fill")
                    .font(.system(size: 9))
                    .foregroundStyle(Theme.accent)
            }
            Text(relativeTimestamp)
                .font(Tokens.Typeface.cardTimestamp)
                .foregroundStyle(Theme.secondaryText)
            Spacer(minLength: 0)
            if hovering {
                Image(systemName: "ellipsis")
                    .font(.system(size: 10))
                    .foregroundStyle(Theme.secondaryText)
            }
        }
    }

    private func thumbnail(_ symbol: String) -> some View {
        RoundedRectangle(cornerRadius: Tokens.Radius.image, style: .continuous)
            .fill(tintGradient)
            .frame(height: Tokens.Size.cardThumbHeight)
            .overlay {
                Image(systemName: symbol)
                    .font(.system(size: 30, weight: .light))
                    .foregroundStyle(.white.opacity(0.9))
            }
    }

    private var tintGradient: LinearGradient {
        let colors: [Color]
        switch summary.thumbnailTint ?? .warm {
        case .warm: colors = [.orange.opacity(0.75), .pink.opacity(0.6)]
        case .cool: colors = [.teal.opacity(0.7), .blue.opacity(0.6)]
        case .mono: colors = [.gray.opacity(0.7), .gray.opacity(0.45)]
        case .accent: colors = [Theme.accent.opacity(0.85), .indigo.opacity(0.6)]
        }
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    private var relativeTimestamp: String {
        let cal = Calendar.current
        if cal.isDateInToday(summary.timestamp) {
            return summary.timestamp.formatted(date: .omitted, time: .shortened)
        }
        if cal.isDateInYesterday(summary.timestamp) { return "Yesterday" }
        let days = cal.dateComponents([.day], from: summary.timestamp, to: Date()).day ?? 0
        if days < 7 { return summary.timestamp.formatted(.dateTime.weekday(.wide)) }
        return summary.timestamp.formatted(date: .abbreviated, time: .omitted)
    }
}

#if canImport(PreviewsMacros)
#Preview("Card · thumbnail") {
    ConversationCard(summary: MockData.conversations[1], isSelected: false)
        .frame(width: 200).padding()
}
#Preview("Card · selected, pinned") {
    ConversationCard(summary: MockData.conversations[0], isSelected: true)
        .frame(width: 200).padding()
}
#endif
