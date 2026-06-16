import SwiftUI
import Observation
import EchoKit

/// 应用级状态。Phase 1 用 mock 填充；Phase 3 接 GatewayClient 事件流。
@MainActor
@Observable
final class AppState {

    // 连接
    enum Connection: Equatable { case offline, connecting, online }
    var connection: Connection = .offline

    // 侧栏
    var conversations: [ConversationSummary] = []
    var selectedConversationId: String?

    // 当前对话
    var transcript: [TranscriptItem] = []
    var statusLine: String?          // 顶部状态条（Thinking…/Running tool…）
    var isResponding = false
    var composerText = ""

    // Echo 信号
    var ratingQueue: [RatingItem] = []
    var scopeQuestion: ScopeQuestion?
    var clarifyPrompt: ClarifyPrompt?

    // Echo 侧面板（M4 置信度 / M1 候选 / M5 偏好 / 状态）
    var showEchoPanel = false
    var echoSkills: [SkillConfidence] = []
    var echoCandidates: [EchoCandidate] = []
    var echoPreferences: [Preference] = []
    var echoStatus: EchoStatus?

    var selectedConversation: ConversationSummary? {
        conversations.first { $0.id == selectedConversationId }
    }

    /// 协调器（Phase 3 注入）。nil 时走 Phase 1 本地 mock 行为。
    var coordinator: GatewayCoordinator?

    // 流式累积态
    private var streamingId: String?
    private var streamingText = ""
    private var streamingTools: [ToolActivity] = []
    private var streamingReasoning = ""

    init() {}

    /// 接入真后端：建协调器、spawn gateway、泵事件。
    func connectLive() {
        let coord = GatewayCoordinator(app: self)
        coordinator = coord
        conversations = []
        transcript = []
        Task { await coord.start() }
    }

    /// clarify 应答（M1 提名）。
    func answerClarify(_ answer: String) {
        if let c = clarifyPrompt { coordinator?.respondClarify(requestId: c.id, answer: answer) }
        clarifyPrompt = nil
    }

    /// scope 选择（M2）。
    func chooseScope(_ level: String) {
        if let s = scopeQuestion { coordinator?.submitScope(skillId: s.id, level: level) }
        scopeQuestion = nil
    }

    /// 提交评分反馈（Phase 4 完整 60s/理由语义；此处即时提交）。
    func submitRating(thumb: Int, reason: String?) {
        if let item = ratingQueue.first {
            coordinator?.sendFeedback(invocationId: item.id, rating: thumb, reason: reason)
        }
        commitRating()
    }

    /// 载入 mock（Phase 1 走查 / 预览）。
    static func mock(selected: Bool = true) -> AppState {
        let s = AppState()
        s.connection = .online
        s.conversations = MockData.conversations
        if selected {
            s.selectedConversationId = "c2"
            s.transcript = MockData.sampleTranscript
            s.ratingQueue = MockData.sampleRatings
        }
        return s
    }

    // MARK: - 意图（Phase 1 仅本地模拟；Phase 3 接 gateway）

    func selectConversation(_ id: String) {
        selectedConversationId = id
        clarifyPrompt = nil; scopeQuestion = nil
        if let coordinator {
            transcript = []
            Task { await coordinator.openConversation(id) }
        } else {
            transcript = (id == "c2") ? MockData.sampleTranscript : []
            ratingQueue = (id == "c2") ? MockData.sampleRatings : []
        }
    }

    func newConversation() {
        selectedConversationId = nil
        transcript = []; ratingQueue = []
        scopeQuestion = nil; clarifyPrompt = nil
        if let coordinator { Task { await coordinator.newConversation() } }
    }

    func toggleEchoPanel() {
        showEchoPanel.toggle()
        guard showEchoPanel else { return }
        if let coordinator { coordinator.refreshEchoPanel() }
        else { loadEchoPanelMock() }
    }

    private func loadEchoPanelMock() {
        echoStatus = .init(schemaVersion: 8, encoder: "neural",
                           tableRows: ["echo_signal_event": 124, "echo_skill_confidence": 9])
        echoSkills = [
            .init(skillId: "ascii-art", skillName: "ASCII Art", confidence: 0.42, status: "pending_review", nSignals: 7),
            .init(skillId: "rename-batch", skillName: "Batch Rename", confidence: 0.71, status: "active", nSignals: 12),
            .init(skillId: "research-summary", skillName: "Research Summary", confidence: 0.88, status: "active", nSignals: 20),
        ]
        echoCandidates = [
            .init(id: 142, score: 130, reasons: ["save_intent", "recurrence"]),
            .init(id: 138, score: 60, reasons: ["tool≥5"]),
        ]
        echoPreferences = [
            .init(id: 1, userMessage: "微服务架构图", compositeScore: 0.91, useCount: 3),
            .init(id: 2, userMessage: "marketing email", compositeScore: 0.74, useCount: 1),
        ]
    }

    func deletePreference(_ id: Int) {
        echoPreferences.removeAll { $0.id == id }
        coordinator?.deletePreference(id)
    }

    func togglePin(_ id: String) {
        guard let i = conversations.firstIndex(where: { $0.id == id }) else { return }
        conversations[i].pinned.toggle()
    }

    func deleteConversation(_ id: String) {
        conversations.removeAll { $0.id == id }
        if selectedConversationId == id { newConversation() }
    }

    func send() {
        let text = composerText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        composerText = ""
        transcript.append(.user(.init(id: UUID().uuidString, text: text)))
        if let coordinator { Task { await coordinator.submit(text) } }
    }

    func stop() {
        isResponding = false
        statusLine = nil
        if let coordinator { Task { await coordinator.interrupt() } }
    }

    /// 回放 session.resume 的历史消息到 transcript。
    func loadHistory(_ messages: [TranscriptMessage]) {
        var items: [TranscriptItem] = []
        for m in messages {
            switch m.role {
            case .user:
                items.append(.user(.init(id: UUID().uuidString, text: m.text ?? "")))
            case .assistant:
                items.append(.assistant(.init(id: UUID().uuidString,
                                              blocks: Self.renderBlocks(from: m.text ?? ""))))
            case .system, .tool:
                break
            }
        }
        transcript = items
    }

    func applySessionList(_ items: [SessionListItem]) {
        conversations = items.map { item in
            ConversationSummary(
                id: item.id,
                title: item.title.isEmpty ? String(item.preview.prefix(28)) : item.title,
                preview: item.preview,
                timestamp: Date(timeIntervalSince1970: item.startedAt)
            )
        }
    }

    /// 评分状态推进。回 idle 视为撤销；提交（从 reason/rated 回 idle）出队。
    func advanceRating(_ newState: RatingItem.RatingState) {
        guard var head = ratingQueue.first else { return }
        let wasRated: Bool
        if case .idle = head.state { wasRated = false } else { wasRated = true }

        if case .idle = newState, wasRated {
            // 从已评分回到 idle：撤销，保留在队首待重评
            head.state = .idle
            ratingQueue[0] = head
            return
        }
        if case .idle = newState, !wasRated {
            // idle→idle 不会发生；忽略
            return
        }
        // 提交路径：reason/rated 之后再回 idle 由上面处理；这里更新中间态
        head.state = newState
        ratingQueue[0] = head
        // Phase 4: 当提交（窗口到期或点提交）时 → EchoAPIClient.sendFeedback + 出队
    }

    /// 提交当前评分并出队（Phase 4 接 /feedback）。
    func commitRating() {
        guard !ratingQueue.isEmpty else { return }
        ratingQueue.removeFirst()
    }

    // MARK: - Phase 3: gateway 事件 → UI 归约

    /// 把一个 gateway 事件映射成 transcript / 状态变更（在 MainActor 上调用）。
    func handle(_ event: ParsedEvent) {
        switch event.event {
        case .ready:
            connection = .online
        case .sessionInfo:
            break // 可在此更新模型/技能元数据
        case .messageStart:
            beginAssistantTurn()
        case .messageDelta(let d):
            streamingText += d.text
            isResponding = true
            refreshStreamingMessage()
        case .messageComplete(let c):
            completeAssistantTurn(text: c.text, usage: c.usage, reasoning: c.reasoning)
        case .statusUpdate(let s):
            statusLine = s.text
        case .toolGenerating(let t):
            upsertTool(.init(id: t.name, name: t.name, state: .running))
        case .toolProgress(let p):
            if let name = p.name { upsertTool(.init(id: name, name: name, preview: p.preview, state: .running)) }
        case .toolComplete(let t):
            let name = t.name ?? "tool"
            upsertTool(.init(id: name, name: name, state: t.error == nil ? .done : .failed,
                             durationS: t.durationS, summary: t.summary ?? t.error))
        case .reasoningDelta(let d), .thinkingDelta(let d):
            streamingReasoning += d.text
            refreshStreamingMessage()
        case .clarifyRequest(let c):
            clarifyPrompt = .init(id: c.requestId, question: c.question, choices: c.choices)
        case .error(let e):
            statusLine = "⚠︎ \(e.displayText)"
            isResponding = false
        case .reasoningAvailable, .approvalRequest, .secretRequest, .other:
            break
        }
    }

    private func beginAssistantTurn() {
        streamingId = UUID().uuidString
        streamingText = ""; streamingReasoning = ""; streamingTools = []
        isResponding = true
        statusLine = statusLine ?? "Thinking…"
        refreshStreamingMessage()
    }

    private func upsertTool(_ tool: ToolActivity) {
        if let i = streamingTools.firstIndex(where: { $0.id == tool.id }) { streamingTools[i] = tool }
        else { streamingTools.append(tool) }
        refreshStreamingMessage()
    }

    /// 用当前累积态刷新（或插入）流式助手消息。
    private func refreshStreamingMessage() {
        guard let id = streamingId else { return }
        let msg = AssistantMessage(
            id: id,
            blocks: streamingText.isEmpty ? [] : [.paragraph(streamingText)],
            toolActivities: streamingTools,
            reasoning: streamingReasoning.isEmpty ? nil : streamingReasoning,
            streaming: true
        )
        if let i = transcript.firstIndex(where: { $0.id == "a-\(id)" }) {
            transcript[i] = .assistant(msg)
        } else {
            transcript.append(.assistant(msg))
        }
    }

    private func completeAssistantTurn(text: String, usage: Usage?, reasoning: String?) {
        let id = streamingId ?? UUID().uuidString
        let msg = AssistantMessage(
            id: id,
            blocks: Self.renderBlocks(from: text),   // Markdown → 富文本多块
            toolActivities: streamingTools,
            reasoning: reasoning ?? (streamingReasoning.isEmpty ? nil : streamingReasoning),
            usage: usage.map { UsageLite(durationS: nil, tokens: $0.total, model: $0.model) },
            streaming: false
        )
        if let i = transcript.firstIndex(where: { $0.id == "a-\(id)" }) {
            transcript[i] = .assistant(msg)
        } else {
            transcript.append(.assistant(msg))
        }
        streamingId = nil; streamingText = ""; streamingReasoning = ""; streamingTools = []
        isResponding = false
        statusLine = nil
        // Phase 4: 拉 /invocations/recent 显示评分
        coordinator?.refreshRatingQueue()
    }

    /// Kit Markdown 块 → UI ResponseBlock。
    static func renderBlocks(from text: String) -> [ResponseBlock] {
        guard !text.isEmpty else { return [] }
        return MarkdownParser.parse(text).map { block in
            switch block {
            case .paragraph(let p): return .paragraph(p)
            case .heading(_, let t): return .heading(t)
            case .bullets(let items): return .bullets(items)
            case .code(let lang, let body): return .code(language: lang, text: body)
            }
        }
    }
}
