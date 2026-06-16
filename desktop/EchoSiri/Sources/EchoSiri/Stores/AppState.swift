import SwiftUI
import Observation
import EchoSiriKit

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

    var selectedConversation: ConversationSummary? {
        conversations.first { $0.id == selectedConversationId }
    }

    init() {}

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
        // Phase 3: gateway.resumeSession(id) → 回放历史
        transcript = (id == "c2") ? MockData.sampleTranscript : []
        ratingQueue = (id == "c2") ? MockData.sampleRatings : []
    }

    func newConversation() {
        // Phase 3: gateway.createSession()
        selectedConversationId = nil
        transcript = []
        ratingQueue = []
        scopeQuestion = nil
        clarifyPrompt = nil
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
        // Phase 3: gateway.submitPrompt(...) → message.start/delta/complete
    }

    func stop() {
        isResponding = false
        statusLine = nil
        // Phase 3: gateway.interrupt(...)
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
}
