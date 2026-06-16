import SwiftUI

/// 助手回复（线框图 W3）：左对齐、无气泡、富文本块流式渲染。
struct AssistantResponse: View {
    let message: AssistantMessage

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // 思考过程（如有）
            if let reasoning = message.reasoning, !reasoning.isEmpty {
                ReasoningBlock(text: reasoning)
            }
            // 工具活动
            ForEach(message.toolActivities) { ToolActivityRow(activity: $0) }

            // 富文本块
            ForEach(message.blocks) { block in
                blockView(block)
            }

            // 流式光标
            if message.streaming {
                TypingCursor()
            }

            // 来源
            if !message.sources.isEmpty {
                SourceChips(sources: message.sources)
            }
            // 用量
            if let usage = message.usage {
                UsageMetaRow(usage: usage)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.trailing, 60)   // 与右对齐用户气泡呼应留白
    }

    @ViewBuilder
    private func blockView(_ block: ResponseBlock) -> some View {
        switch block {
        case .paragraph(let text):
            Text(text)
                .font(Tokens.Typeface.body)
                .foregroundStyle(.primary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        case .heading(let text):
            Text(text)
                .font(Tokens.Typeface.serifTitle)
                .foregroundStyle(.primary)
                .padding(.top, 4)
        case .bullets(let items):
            VStack(alignment: .leading, spacing: 5) {
                ForEach(items, id: \.self) { item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("•").foregroundStyle(Theme.secondaryText)
                        Text(item).font(Tokens.Typeface.body)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        case .image(let img):
            InlineImageCard(image: img)
        case .code(let language, let text):
            CodeBlockView(language: language, text: text)
        }
    }
}

/// 流式打字光标。
struct TypingCursor: View {
    @State private var on = true
    var body: some View {
        RoundedRectangle(cornerRadius: 1)
            .fill(Theme.accent)
            .frame(width: 7, height: 16)
            .opacity(on ? 1 : 0.2)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.6).repeatForever()) { on.toggle() }
            }
    }
}

/// 代码块占位（Phase 1c 简版；Phase 1 后段接 macai HighlightedText）。
struct CodeBlockView: View {
    let language: String
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !language.isEmpty {
                Text(language).font(.caption2).foregroundStyle(Theme.secondaryText)
                    .padding(.horizontal, 10).padding(.top, 6)
            }
            ScrollView(.horizontal, showsIndicators: false) {
                Text(text).font(Tokens.Typeface.mono).textSelection(.enabled)
                    .padding(10)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .insetSurface(cornerRadius: Tokens.Radius.button)
    }
}

#if canImport(PreviewsMacros)
#Preview("Assistant response") {
    ScrollView {
        if case .assistant(let m) = MockData.sampleTranscript[1] {
            AssistantResponse(message: m).padding()
        }
    }
    .frame(width: 640, height: 720)
}
#endif
