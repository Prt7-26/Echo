import SwiftUI

/// 用户消息气泡（线框图 W1）：右对齐、浅灰圆角 pill。
struct UserBubble: View {
    let message: UserMessage

    var body: some View {
        HStack {
            Spacer(minLength: 60)
            Text(message.text)
                .font(Tokens.Typeface.body)
                .foregroundStyle(.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(Theme.userBubble, in: Capsule(style: .continuous))
                .textSelection(.enabled)
        }
    }
}

#if canImport(PreviewsMacros)
#Preview {
    VStack(spacing: 12) {
        UserBubble(message: .init(id: "1", text: "What's the largest park in this city?"))
        UserBubble(message: .init(id: "2", text: "Short one"))
    }
    .padding()
    .frame(width: 520)
}
#endif
