import SwiftUI
import AppKit   // NSPasteboard（代码块复制）

/// 助手回复（线框图 W3）：左对齐、无气泡、富文本块流式渲染。
struct AssistantResponse: View {
    let message: AssistantMessage
    var app: AppState? = nil   // 内联操作行用；预览可省
    @State private var reason = ""
    @FocusState private var reasonFocused: Bool

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
            // 内联操作行（流式结束后才显示，对齐 Claude）：copy / retry 常驻；
            // 👍/👎 仅当本轮调用了技能（有 invocation）才显示——与 TUI 一致；
            // 点过赞/踩后，行右侧弹出短单行理由框（可选，回车提交，过 LLM 校准置信度）。
            if !message.streaming {
                HStack(spacing: 2) {
                    iconButton("doc.on.doc", help: "复制") { copyPlainText() }
                    if app != nil {
                        iconButton("arrow.clockwise", help: "重试") { app?.retry(message.id) }
                    }
                    if app != nil, message.invocationId != nil {
                        inlineThumb("hand.thumbsup", value: 1)
                        inlineThumb("hand.thumbsdown", value: -1)
                        if message.rating != nil {
                            TextField("说说理由（可选，回车提交）", text: $reason)
                                .textFieldStyle(.plain)
                                .font(Tokens.Typeface.metaSmall)
                                .frame(width: 190)
                                .focused($reasonFocused)
                                .onSubmit {
                                    app?.submitRatingReason(message.id, reason: reason)
                                    reasonFocused = false
                                }
                                .padding(.horizontal, 9).padding(.vertical, 3)
                                .background(Capsule().fill(Theme.insetSurface))
                                .padding(.leading, 4)
                                .transition(.opacity.combined(with: .move(edge: .leading)))
                        }
                    }
                }
                .padding(.top, 2)
                .animation(.smooth(duration: 0.18), value: message.rating)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.trailing, 60)   // 与右对齐用户气泡呼应留白
    }

    /// 小图标按钮（灰色低调，悬停可点）。
    private func iconButton(_ symbol: String, help: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12))
                .foregroundStyle(Theme.secondaryText)
                .frame(width: 28, height: 24)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .help(help)
    }

    private func inlineThumb(_ base: String, value: Int) -> some View {
        let selected = message.rating == value
        let tint: Color = value > 0 ? Theme.Signal.positive : Theme.Signal.negative
        return Button { app?.rateMessage(message.id, thumb: value); reasonFocused = true } label: {
            Image(systemName: selected ? "\(base).fill" : base)
                .font(.system(size: 12))
                .foregroundStyle(selected ? tint : Theme.secondaryText)
                .frame(width: 28, height: 24)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .help(value > 0 ? "有用" : "没用")
    }

    /// 回复纯文本（供复制）。
    private func copyPlainText() {
        let text = message.blocks.map { b -> String in
            switch b {
            case .paragraph(let s): return s
            case .heading(let s): return s
            case .bullets(let items): return items.map { "• \($0)" }.joined(separator: "\n")
            case .code(_, let t): return t
            case .image: return ""
            }
        }.joined(separator: "\n\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
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
