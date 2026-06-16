import SwiftUI

/// 评分 widget（线框图 W5a）：idle(👍/👎) → rated(60s 撤销/补充理由) → reason。
struct RatingWidget: View {
    let item: RatingItem
    /// 回传新状态给 AppState（提交/撤销/补充理由）。
    let onChange: (RatingItem.RatingState) -> Void

    @State private var reasonText = ""

    var body: some View {
        Group {
            switch item.state {
            case .idle: idleView
            case .rated(let thumb): ratedView(thumb)
            case .reason(let thumb): reasonView(thumb)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .glassCard(cornerRadius: Tokens.Radius.card)
    }

    private var idleView: some View {
        HStack {
            Text("这条有用吗？").font(.callout).foregroundStyle(Theme.secondaryText)
            Spacer()
            thumbButton("hand.thumbsup", value: 1)
            thumbButton("hand.thumbsdown", value: -1)
        }
    }

    private func ratedView(_ thumb: Int) -> some View {
        HStack {
            Label("已记录 \(thumb > 0 ? "👍" : "👎")", systemImage: "checkmark.circle.fill")
                .font(.callout).foregroundStyle(thumb > 0 ? .green : Theme.Signal.negative)
            Spacer()
            Button("撤销") { onChange(.idle) }.buttonStyle(.plain).foregroundStyle(Theme.accent)
            Button { onChange(.reason(thumb: thumb)) } label: {
                Label("补充理由", systemImage: "square.and.pencil")
            }.buttonStyle(.plain).foregroundStyle(Theme.accent)
        }
        .font(.callout)
    }

    private func reasonView(_ thumb: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("说说哪里好/不好（LLM 会按你的话校准置信度）…", text: $reasonText, axis: .vertical)
                .textFieldStyle(.plain).lineLimit(2...4)
                .padding(8).glassPanel(cornerRadius: Tokens.Radius.button)
            HStack {
                Spacer()
                Button("取消") { onChange(.rated(thumb: thumb)) }.buttonStyle(.plain)
                Button("提交") { onChange(.idle) }.buttonStyle(.borderedProminent).tint(Theme.accent)
            }.font(.callout)
        }
    }

    private func thumbButton(_ symbol: String, value: Int) -> some View {
        Button { onChange(.rated(thumb: value)) } label: {
            Image(systemName: symbol).font(.system(size: 15))
        }
        .buttonStyle(.glassIcon)
    }
}

#if canImport(PreviewsMacros)
#Preview("idle") {
    RatingWidget(item: .init(id: 1, skillName: "research", state: .idle)) { _ in }
        .frame(width: 520).padding()
}
#Preview("rated") {
    RatingWidget(item: .init(id: 1, skillName: "research", state: .rated(thumb: 1))) { _ in }
        .frame(width: 520).padding()
}
#Preview("reason") {
    RatingWidget(item: .init(id: 1, skillName: "research", state: .reason(thumb: -1))) { _ in }
        .frame(width: 520).padding()
}
#endif
