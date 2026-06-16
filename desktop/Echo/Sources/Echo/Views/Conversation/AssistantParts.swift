import SwiftUI

// 助手回复的零件（线框图 W3/W4）。

/// 内联图片卡：圆角横幅 + 轻投影（mock 用渐变 + SF Symbol 占位）。
struct InlineImageCard: View {
    let image: ImageBlock

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            RoundedRectangle(cornerRadius: Tokens.Radius.image, style: .continuous)
                .fill(gradient)
                .frame(height: 200)
                .overlay {
                    Image(systemName: image.symbol)
                        .font(.system(size: 44, weight: .ultraLight))
                        .foregroundStyle(.white.opacity(0.9))
                }
                .shadow(color: .black.opacity(0.10), radius: 6, y: 2)
            if let caption = image.caption {
                Text(caption).font(.caption).foregroundStyle(Theme.secondaryText)
            }
        }
    }

    private var gradient: LinearGradient {
        let colors: [Color]
        switch image.tint {
        case .warm: colors = [.orange.opacity(0.7), .pink.opacity(0.55)]
        case .cool: colors = [.teal.opacity(0.65), .blue.opacity(0.55)]
        case .mono: colors = [.gray.opacity(0.6), .gray.opacity(0.4)]
        case .accent: colors = [Theme.accent.opacity(0.8), .indigo.opacity(0.55)]
        }
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }
}

/// 来源胶囊（"Encyclopedia  +2"）。
struct SourceChips: View {
    let sources: [String]

    var body: some View {
        HStack(spacing: 6) {
            ForEach(sources, id: \.self) { s in
                Text(s)
                    .font(.caption2)
                    .foregroundStyle(Theme.secondaryText)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .glassCard(cornerRadius: Tokens.Radius.chip)
            }
        }
    }
}

/// 用量 meta 行（淡）。
struct UsageMetaRow: View {
    let usage: UsageLite

    var body: some View {
        HStack(spacing: 10) {
            if let d = usage.durationS { label("clock", String(format: "%.1fs", d)) }
            if let t = usage.tokens { label("number", "\(t) tok") }
            if let m = usage.model { label("cpu", m) }
        }
        .font(.caption2)
        .foregroundStyle(Theme.secondaryText.opacity(0.8))
    }

    private func label(_ symbol: String, _ text: String) -> some View {
        HStack(spacing: 3) {
            Image(systemName: symbol); Text(text)
        }
    }
}

/// 工具活动行（线框图 W4）：进行中 / 完成折叠 / 失败。
struct ToolActivityRow: View {
    let activity: ToolActivity
    @State private var expanded = false

    var body: some View {
        HStack(spacing: 8) {
            icon
            Text(activity.name).font(.callout.monospaced())
            if let preview = activity.preview, activity.state == .running {
                Text(preview).font(.caption).foregroundStyle(Theme.secondaryText).lineLimit(1)
            }
            if let summary = activity.summary, activity.state != .running {
                Text("· \(summary)").font(.caption).foregroundStyle(Theme.secondaryText)
            }
            Spacer(minLength: 0)
            if let d = activity.durationS, activity.state != .running {
                Text(String(format: "%.1fs", d)).font(.caption2).foregroundStyle(Theme.secondaryText)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .glassCard(cornerRadius: Tokens.Radius.button)
    }

    @ViewBuilder private var icon: some View {
        switch activity.state {
        case .running:
            Image(systemName: "wrench.adjustable")
                .foregroundStyle(Theme.accent)
                .symbolEffect(.pulse)
        case .done:
            Image(systemName: "checkmark").foregroundStyle(.green)
        case .failed:
            Image(systemName: "xmark").foregroundStyle(Theme.Signal.negative)
        }
    }
}

/// 思考过程折叠块（线框图 W4）。
struct ReasoningBlock: View {
    let text: String
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(.smooth(duration: 0.18)) { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "brain")
                    Text("Reasoning")
                    Image(systemName: expanded ? "chevron.down" : "chevron.right").font(.caption2)
                    Spacer(minLength: 0)
                }
                .font(.caption).foregroundStyle(Theme.secondaryText)
            }
            .buttonStyle(.plain)

            if expanded {
                Text(text)
                    .font(.caption)
                    .foregroundStyle(Theme.secondaryText)
                    .textSelection(.enabled)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .glassCard(cornerRadius: Tokens.Radius.button)
    }
}
