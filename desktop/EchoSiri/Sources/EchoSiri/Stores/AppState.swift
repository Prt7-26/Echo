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
}
