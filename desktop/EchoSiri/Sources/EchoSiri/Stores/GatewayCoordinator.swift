import Foundation
import EchoSiriKit

/// 把 GatewayClient 的事件流泵进 AppState，并把 UI 意图翻译成 gateway 调用。
/// 聊天走 stdio gateway；Echo 信号走 dashboard REST（若在跑）。
@MainActor
final class GatewayCoordinator {
    private let client = GatewayClient()
    private let echo: EchoAPIClient
    private weak var app: AppState?

    /// 当前对话的 gateway session id（prompt.submit 用）。
    private var currentSessionId: String?
    /// 评分作用域用的 Hermes session_key（来自事件 session_key / create 响应）。
    private var sessionKey: String?
    private var pump: Task<Void, Never>?
    private var monitors: SignalMonitors?

    init(app: AppState, dashboardBase: URL = BackendLocator.dashboardBase()) {
        self.app = app
        self.echo = EchoAPIClient(base: dashboardBase)
    }

    /// 启动：spawn gateway → 连接 → 泵事件 → 载入会话列表。
    func start() async {
        guard let resolved = BackendLocator.resolve() else {
            app?.statusLine = "找不到 Echo 后端（设 ECHO_REPO_ROOT）"
            return
        }
        app?.connection = .connecting
        do {
            let transport = try StdioSubprocessTransport(
                pythonPath: resolved.python, repoRoot: resolved.repoRoot)
            await client.connect(transport)
            pump = Task { [weak self] in
                guard let self, let events = await self.clientEvents() else { return }
                for await ev in events { await self.route(ev) }
            }
            startSignalMonitors()
            await loadSessions()
        } catch {
            app?.statusLine = "后端启动失败：\(error)"
            app?.connection = .offline
        }
    }

    private func clientEvents() async -> AsyncStream<ParsedEvent>? { client.events }

    private func route(_ ev: ParsedEvent) async {
        if let key = ev.sessionKey { sessionKey = key }
        app?.handle(ev)
    }

    // MARK: 意图

    func loadSessions() async {
        if let items = try? await client.listSessions() { app?.applySessionList(items) }
    }

    func openConversation(_ id: String) async {
        currentSessionId = id
        if let resumed = try? await client.resumeSession(id) {
            sessionKey = resumed.info.flatMap { _ in resumed.sessionId } ?? id
            app?.loadHistory(resumed.messages)
        }
    }

    func newConversation() async {
        if let created = try? await client.createSession() {
            currentSessionId = created.sessionId
            sessionKey = created.sessionId
            app?.selectedConversationId = created.sessionId
        }
    }

    func submit(_ text: String) async {
        // 没有当前会话先建一个（空态首条消息）。
        if currentSessionId == nil { await newConversation() }
        guard let sid = currentSessionId else { return }
        _ = try? await client.submitPrompt(session: sid, text: text)
    }

    func interrupt() async {
        guard let sid = currentSessionId else { return }
        _ = try? await client.interrupt(session: sid)
    }

    // MARK: Echo 信号（dashboard REST）

    func refreshRatingQueue() {
        guard let key = sessionKey else { return }
        Task { [weak self] in
            guard let self else { return }
            if let invs = try? await self.echo.recentInvocations(sessionId: key) {
                let items = invs.filter { !($0.rated ?? false) }
                    .map { RatingItem(id: $0.id, skillName: $0.skillName ?? "skill") }
                self.app?.ratingQueue = items
            }
            if let pending = try? await self.echo.pendingScope(sessionId: key), let first = pending.first {
                self.app?.scopeQuestion = .init(id: first.skillId, skillName: first.skillName ?? "skill")
            }
        }
    }

    func sendFeedback(invocationId: Int, rating: Int, reason: String?) {
        Task { [weak self] in
            try? await self?.echo.sendFeedback(
                .init(invocationId: invocationId, rating: rating, reason: reason, sessionId: self?.sessionKey))
        }
    }

    func submitScope(skillId: String, level: String) {
        Task { [weak self] in
            try? await self?.echo.submitScope(.init(skillId: skillId, level: level, sessionId: self?.sessionKey))
        }
    }

    /// clarify 应答（M1 提名）。
    func respondClarify(requestId: String, answer: String) {
        guard let sid = currentSessionId else { return }
        Task { [weak self] in
            _ = try? await self?.client.respondClarify(session: sid, requestId: requestId, answer: answer)
        }
    }

    private func startSignalMonitors() {
        let echo = self.echo   // 端点服务端按最近 invocation 归属，无需带 session_key
        let m = SignalMonitors { body in
            Task { try? await echo.clipboardSignal(body) }
        }
        monitors = m
        m.start()
    }

    func shutdown() async {
        monitors?.stop()
        pump?.cancel()
        await client.disconnect()
    }
}
