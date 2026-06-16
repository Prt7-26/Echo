import SwiftUI

/// 评分 widget（线框图 W5a）：idle(👍/👎) → rated(60s 撤销/补充理由) → reason。
///
/// 提交语义（对齐 dashboard bundle，CLAUDE.md Step 23）：
/// - 点 👍/👎 仅进入 rated 态并起 **60s 撤销窗**，**不**立即发反馈；
/// - 窗口到期 → `onCommit(thumb, nil)` 提交基础评分；
/// - 撤销 → 回 idle、计时停（真取消，不发反馈）；
/// - 补充理由 → 提交时 `onCommit(thumb, reason)`，理由会过 LLM reason_score 校准置信度。
struct RatingWidget: View {
    let item: RatingItem
    /// 纯 UI 态切换（点赞/撤销/展开理由）。
    let onState: (RatingItem.RatingState) -> Void
    /// 真正提交反馈并出队（thumb + 可选 reason）。
    let onCommit: (Int, String?) -> Void

    @State private var reasonText = ""
    @State private var secondsLeft = Self.window

    private static let window = 60

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
            Text("这条有用吗？").font(Tokens.Typeface.callout).foregroundStyle(Theme.secondaryText)
            Spacer()
            thumbButton("hand.thumbsup", value: 1)
            thumbButton("hand.thumbsdown", value: -1)
        }
    }

    private func ratedView(_ thumb: Int) -> some View {
        HStack(spacing: 12) {
            Label("已记录 \(thumb > 0 ? "👍" : "👎")", systemImage: "checkmark.circle.fill")
                .font(Tokens.Typeface.callout).foregroundStyle(thumb > 0 ? .green : Theme.Signal.negative)
            Spacer()
            // 60s 撤销窗倒计时（细弱次级，不抢眼）
            Text("\(secondsLeft)s")
                .font(Tokens.Typeface.metaSmall.monospacedDigit())
                .foregroundStyle(Theme.secondaryText)
            Button("撤销") { onState(.idle) }
                .buttonStyle(.plain).foregroundStyle(Theme.accent)
            Button { onState(.reason(thumb: thumb)) } label: {
                Label("补充理由", systemImage: "square.and.pencil")
            }.buttonStyle(.plain).foregroundStyle(Theme.accent)
        }
        .font(Tokens.Typeface.callout)
        // 进入 rated 态即开 60s 倒计时窗口；切走（撤销/补充理由）则 .task 取消。
        .task(id: item.id) {
            secondsLeft = Self.window
            while secondsLeft > 0 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if Task.isCancelled { return }
                secondsLeft -= 1
            }
            onCommit(thumb, nil)   // 窗口到期 → 提交基础评分
        }
    }

    private func reasonView(_ thumb: Int) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("说说哪里好/不好（LLM 会按你的话校准置信度）…", text: $reasonText, axis: .vertical)
                .textFieldStyle(.plain).lineLimit(2...4)
                .padding(8).glassPanel(cornerRadius: Tokens.Radius.button)
            HStack {
                Spacer()
                Button("取消") { onState(.rated(thumb: thumb)) }.buttonStyle(.plain)
                Button("提交") { onCommit(thumb, reasonText) }
                    .buttonStyle(.borderedProminent).tint(Theme.accent)
            }.font(Tokens.Typeface.callout)
        }
    }

    private func thumbButton(_ symbol: String, value: Int) -> some View {
        Button { onState(.rated(thumb: value)) } label: {
            Image(systemName: symbol).font(.system(size: 15))
        }
        .buttonStyle(.glassIcon)
    }
}

#if canImport(PreviewsMacros)
#Preview("idle") {
    RatingWidget(item: .init(id: 1, skillName: "research", state: .idle), onState: { _ in }, onCommit: { _, _ in })
        .frame(width: 520).padding()
}
#Preview("rated") {
    RatingWidget(item: .init(id: 1, skillName: "research", state: .rated(thumb: 1)), onState: { _ in }, onCommit: { _, _ in })
        .frame(width: 520).padding()
}
#Preview("reason") {
    RatingWidget(item: .init(id: 1, skillName: "research", state: .reason(thumb: -1)), onState: { _ in }, onCommit: { _, _ in })
        .frame(width: 520).padding()
}
#endif
