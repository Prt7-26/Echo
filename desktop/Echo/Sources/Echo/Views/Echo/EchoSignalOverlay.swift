import SwiftUI

/// 对话区底部、输入条上方的 Echo 信号叠层（线框图 W5）。
/// 优先级：clarify(M1) > scope(M2) > rating。同时只展示最高优先级一项。
struct EchoSignalOverlay: View {
    @Bindable var app: AppState

    var body: some View {
        Group {
            if let clarify = app.clarifyPrompt {
                ClarifyCard(prompt: clarify) { answer in app.answerClarify(answer) }
            } else if let scope = app.scopeQuestion {
                ScopeQuestionCard(question: scope) { level in app.chooseScope(level) }
            } else if let rating = app.ratingQueue.first {
                RatingWidget(item: rating) { newState in
                    app.advanceRating(newState)
                    // 首次给出 👍/👎 即提交反馈（Phase 4 再加 60s/理由细化）
                    if case .rated(let thumb) = newState { app.submitRating(thumb: thumb, reason: nil) }
                }
            }
        }
        .padding(.horizontal, Tokens.Spacing.loose)
        .animation(.smooth(duration: 0.2), value: app.clarifyPrompt)
        .animation(.smooth(duration: 0.2), value: app.scopeQuestion)
        .animation(.smooth(duration: 0.2), value: app.ratingQueue)
    }
}

#if canImport(PreviewsMacros)
#Preview("Rating") {
    let app = AppState.mock()
    return EchoSignalOverlay(app: app).frame(width: 680).padding()
}
#Preview("Clarify") {
    let app = AppState.mock()
    app.clarifyPrompt = MockData.sampleClarify
    return EchoSignalOverlay(app: app).frame(width: 680).padding()
}
#Preview("Scope") {
    let app = AppState.mock()
    app.scopeQuestion = MockData.sampleScope
    return EchoSignalOverlay(app: app).frame(width: 680).padding()
}
#endif
