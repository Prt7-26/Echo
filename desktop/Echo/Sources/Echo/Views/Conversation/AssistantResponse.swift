import SwiftUI
import AppKit   // NSPasteboard（代码块复制）

/// 助手回复（线框图 W3）：左对齐、无气泡、富文本块流式渲染。
struct AssistantResponse: View {
    let message: AssistantMessage
    var app: AppState? = nil   // 内联点赞用；预览可省

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
            // 内联操作行：回复末尾的 👍/👎（流式结束后才显示，对齐 Claude）。
            if !message.streaming, app != nil {
                HStack(spacing: 2) {
                    inlineThumb("hand.thumbsup", value: 1)
                    inlineThumb("hand.thumbsdown", value: -1)
                }
                .padding(.top, 2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.trailing, 60)   // 与右对齐用户气泡呼应留白
    }

    private func inlineThumb(_ base: String, value: Int) -> some View {
        let selected = message.rating == value
        let tint: Color = value > 0 ? Theme.Signal.positive : Theme.Signal.negative
        return Button { app?.rateMessage(message.id, thumb: value) } label: {
            Image(systemName: selected ? "\(base).fill" : base)
                .font(.system(size: 12))
                .foregroundStyle(selected ? tint : Theme.secondaryText)
                .frame(width: 28, height: 24)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .help(value > 0 ? "有用" : "没用")
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

/// 代码块：语言标签 + 复制按钮 + 横向滚动等宽（可选中）。
/// 语法高亮（macai HighlightedText）属后续视觉打磨；当前优先把「复制」做对，最常用。
struct CodeBlockView: View {
    let language: String
    let text: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                if !language.isEmpty {
                    Text(language).font(Tokens.Typeface.metaSmall).foregroundStyle(Theme.secondaryText)
                }
                Spacer(minLength: 0)
                Button(action: copy) {
                    Label(copied ? "已复制" : "复制", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .font(Tokens.Typeface.metaSmall).labelStyle(.titleAndIcon)
                }
                .buttonStyle(.plain)
                .foregroundStyle(copied ? Theme.Signal.positive : Theme.secondaryText)
            }
            .padding(.horizontal, 10).padding(.top, 6)
            ScrollView(.horizontal, showsIndicators: false) {
                Text(text).font(Tokens.Typeface.mono).textSelection(.enabled)
                    .padding(10)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .insetSurface(cornerRadius: Tokens.Radius.button)
    }

    private func copy() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        copied = true
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            copied = false
        }
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
