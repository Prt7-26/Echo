import SwiftUI
import EchoKit

/// Echo 侧面板（线框图 W6）：状态 + M4 置信度排名 + M1 候选 + M5 偏好库。
/// 复用已验证的 EchoAPIClient（经 GatewayCoordinator）。
struct EchoSidePanel: View {
    @Bindable var app: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Tokens.Spacing.content) {
                header
                if let s = app.echoStatus { statusStrip(s) }
                section("置信度排名 (M4)") { confidenceList }
                section("新技能候选 (M1)") { candidateList }
                section("偏好库 (M5)") { preferenceList }
            }
            .padding(Tokens.Spacing.content)
        }
        .frame(minWidth: 240, idealWidth: 300, maxWidth: 360)
        .background(WindowGlassBackground().ignoresSafeArea())
    }

    private var header: some View {
        HStack {
            Image(systemName: "waveform.path.ecg").foregroundStyle(Theme.accent)
            Text("ECHO").font(.headline).tracking(2)
            Spacer()
            Button { app.toggleEchoPanel() } label: { Image(systemName: "sidebar.right") }
                .buttonStyle(.glassIcon)
        }
    }

    private func statusStrip(_ s: EchoStatus) -> some View {
        HStack(spacing: 12) {
            if let v = s.schemaVersion { tag("schema v\(v)") }
            if let e = s.encoder { tag(e == "neural" ? "neural ⚡︎" : e) }
        }
        .font(.caption2)
    }

    private func tag(_ t: String) -> some View {
        Text(t).padding(.horizontal, 8).padding(.vertical, 3).glassCard(cornerRadius: Tokens.Radius.chip)
    }

    @ViewBuilder
    private func section<Content: View>(_ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            content()
        }
    }

    // M4 置信度
    private var confidenceList: some View {
        VStack(spacing: 6) {
            ForEach(app.echoSkills.sorted { $0.confidence < $1.confidence }) { sk in
                HStack(spacing: 8) {
                    ConfidenceBar(value: sk.confidence)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(sk.skillName ?? sk.skillId).font(.caption).lineLimit(1)
                        if let st = sk.status { Text(st).font(.caption2).foregroundStyle(.secondary) }
                    }
                    Spacer(minLength: 0)
                    Text(String(format: "%.2f", sk.confidence))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Theme.confidenceColor(sk.confidence))
                }
                .padding(8).glassCard(cornerRadius: Tokens.Radius.button)
            }
            if app.echoSkills.isEmpty { emptyHint("暂无技能") }
        }
    }

    // M1 候选
    private var candidateList: some View {
        VStack(spacing: 6) {
            ForEach(app.echoCandidates) { c in
                HStack {
                    Text("#\(c.id)").font(.caption.monospaced())
                    Text("score \(c.score)").font(.caption).foregroundStyle(Theme.accent)
                    Spacer(minLength: 0)
                    if let reasons = c.reasons {
                        Text(reasons.joined(separator: "·")).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .padding(8).glassCard(cornerRadius: Tokens.Radius.button)
            }
            if app.echoCandidates.isEmpty { emptyHint("暂无候选") }
        }
    }

    // M5 偏好
    private var preferenceList: some View {
        VStack(spacing: 6) {
            ForEach(app.echoPreferences) { p in
                HStack {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(p.userMessage ?? "—").font(.caption).lineLimit(1)
                        if let s = p.compositeScore {
                            Text(String(format: "%.2f · ×%d", s, p.useCount ?? 0))
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    Spacer(minLength: 0)
                    Button { app.deletePreference(p.id) } label: { Image(systemName: "trash") }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                }
                .padding(8).glassCard(cornerRadius: Tokens.Radius.button)
            }
            if app.echoPreferences.isEmpty { emptyHint("暂无偏好") }
        }
    }

    private func emptyHint(_ t: String) -> some View {
        Text(t).font(.caption2).foregroundStyle(.secondary.opacity(0.6))
    }
}

/// 置信度小条。
struct ConfidenceBar: View {
    let value: Double
    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(.secondary.opacity(0.2))
                Capsule().fill(Theme.confidenceColor(value))
                    .frame(width: geo.size.width * max(0.05, min(1, value)))
            }
        }
        .frame(width: 36, height: 6)
    }
}

#if canImport(PreviewsMacros)
#Preview {
    let app = AppState.mock()
    app.toggleEchoPanel()
    return EchoSidePanel(app: app).frame(width: 300, height: 700)
}
#endif
