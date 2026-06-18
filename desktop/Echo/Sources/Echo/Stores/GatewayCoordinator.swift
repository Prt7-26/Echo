import Foundation
import EchoKit

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
    private var resolved: BackendLocator.Resolved?
    private var supervisor: Task<Void, Never>?
    private var backoff = ExponentialBackoff(base: 1, factor: 2, cap: 30)
    /// 当前子进程 transport，重连前先关掉它（否则每次重连泄漏一个 python 进程）。
    private var transport: StdioSubprocessTransport?
    /// 子进程最近的 stderr 尾巴——启动失败时用来给用户一句人话的原因。
    private let stderrTail = StderrTail()

    init(app: AppState, dashboardBase: URL = BackendLocator.dashboardBase()) {
        self.app = app
        self.echo = EchoAPIClient(base: dashboardBase)
    }

    /// 启动：spawn gateway → 连接 → 泵事件 → 载入会话列表 + 监督重连。
    func start() async {
        guard let r = BackendLocator.resolve() else {
            app?.statusLine = "找不到 Echo 后端（设 ECHO_REPO_ROOT）"
            return
        }
        resolved = r
        // 事件泵只建一次（events 流在 client 生命周期内复用，跨重连不变）。
        // 用 detached：迭代循环跑在后台执行器，不占 MainActor。每条事件再 await 跳到
        // MainActor 的 route（顺序由 await 串行保证），流式洪峰时主线程只做轻量归约、不被迭代占满。
        pump = Task.detached(priority: .userInitiated) { [weak self] in
            guard let self, let events = await self.clientEvents() else { return }
            for await ev in events { await self.route(ev) }
        }
        await connectOnce()
        startSignalMonitors()
        superviseReconnect()
    }

    private func connectOnce() async {
        guard let r = resolved else { return }
        app?.connection = .connecting
        // 先关掉上一个子进程（重连场景），否则 python 进程 + 接收循环会逐次泄漏。
        if let old = transport { await old.close(); transport = nil }
        do {
            // 关键：spawn（Process.run + 建管道）是阻塞调用。GatewayCoordinator 是 @MainActor，
            // 若直接在这里 new，fork/exec 会卡在主线程——重连风暴时表现为沙滩球。挪到后台执行器。
            let tail = stderrTail
            let t = try await Task.detached(priority: .userInitiated) {
                try StdioSubprocessTransport(
                    pythonPath: r.python, repoRoot: r.repoRoot,
                    onStderrLine: { line in tail.append(line) })
            }.value
            transport = t
            await client.connect(t)
            await client.setCallTimeout(12)   // 调用卡住时 12s 即失败，UI 不至于死等 30s
            // 注意：不在此 reset backoff——只有真正收到 gateway.ready（route 里）才算连上，
            // 否则子进程立刻崩溃（如缺依赖）时 backoff 永远归零 → 疯狂重启风暴。
            await loadSessions()
        } catch {
            app?.statusLine = "后端启动失败：\(error)"
            app?.connection = .offline
        }
    }

    /// 监督：transport 断开（state==.failed）时指数退避后重连。
    /// backoff 仅在 route 收到 .ready 时复位，所以崩溃循环会被退避到 30s 间隔，不再风暴。
    private func superviseReconnect() {
        supervisor = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self else { return }
                if await self.client.state == .failed {
                    let delay = self.backoff.next() ?? 30
                    let why = self.stderrTail.lastMeaningful()
                    self.app?.statusLine = why.map { "后端退出（\($0)），\(Int(delay))s 后重连…" }
                        ?? "连接断开，\(Int(delay))s 后重连…"
                    self.app?.connection = .connecting
                    try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    await self.connectOnce()
                }
            }
        }
    }

    private func clientEvents() async -> AsyncStream<ParsedEvent>? { client.events }

    private func route(_ ev: ParsedEvent) async {
        if let key = ev.sessionKey { sessionKey = key }
        if case .ready = ev.event { backoff.reset() }   // 真连上才复位退避
        app?.handle(ev)
    }

    // MARK: 意图

    func loadSessions() async {
        if let items = try? await client.listSessions() { app?.applySessionList(items) }
    }

    /// 刷新会话列表（新建会话发完首条消息后，让侧栏出现这条新会话）。
    func refreshSessions() { Task { [weak self] in await self?.loadSessions() } }

    func openConversation(_ id: String) async {
        currentSessionId = id
        let resumed = try? await client.resumeSession(id)
        // resume 往返期间用户可能已切到别的会话——丢弃迟到的响应，别把 sessionKey/历史串台。
        guard currentSessionId == id else { return }
        if let resumed {
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

    /// 删除后端会话（侧栏删除时调用，避免重启后复现）。
    func deleteSession(_ id: String) {
        Task { [weak self] in _ = try? await self?.client.deleteSession(id) }
    }

    // MARK: Echo 信号（dashboard REST）

    /// 已关联过的最大 invocation id。只把「比它更新」的 invocation 关联到回复 →
    /// 即本轮真的调用了技能才显示点赞（没调技能时最近 invocation 还是旧的，不显示）——与 TUI 一致。
    private var lastInvocationId = 0

    /// 回复完成后拉最近 invocation；若本轮产生了新 invocation，关联到最后一条助手消息。
    func refreshRatingQueue() {
        guard let key = sessionKey else { return }
        Task { [weak self] in
            guard let self else { return }
            guard let invs = try? await self.echo.recentInvocations(sessionId: key),
                  let newest = invs.map(\.id).max() else { return }
            if newest > self.lastInvocationId {
                self.lastInvocationId = newest
                self.app?.attachInvocationToLastMessage(newest)
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

    /// 刷新 Echo 侧面板（M4 置信度 / M1 候选 / M5 偏好 / 状态）。
    func refreshEchoPanel() {
        Task { [weak self] in
            guard let self else { return }
            async let skills = try? self.echo.skills()
            async let cands = try? self.echo.candidates()
            async let prefs = try? self.echo.preferences()
            async let status = try? self.echo.status()
            let (s, c, p, st) = await (skills, cands, prefs, status)
            self.app?.echoSkills = s ?? []
            self.app?.echoCandidates = c ?? []
            self.app?.echoPreferences = p ?? []
            self.app?.echoStatus = st
        }
    }

    func deletePreference(_ id: Int) {
        Task { [weak self] in try? await self?.echo.deletePreference(id) }
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
        supervisor?.cancel()
        monitors?.stop()
        pump?.cancel()
        if let t = transport { await t.close(); transport = nil }
        await client.disconnect()
    }
}

/// 子进程 stderr 的线程安全环形尾巴：启动失败时给用户一句人话的原因
/// （如「No module named 'dotenv'」），而不是干等「连接中」。
final class StderrTail: @unchecked Sendable {
    private let lock = NSLock()
    private var lines: [String] = []

    func append(_ line: String) {
        let s = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return }
        lock.lock(); defer { lock.unlock() }
        lines.append(s)
        if lines.count > 12 { lines.removeFirst(lines.count - 12) }
    }

    /// 最有信息量的一行：优先 Error/Exception，否则最后一行非堆栈帧。
    func lastMeaningful() -> String? {
        lock.lock(); defer { lock.unlock() }
        if let err = lines.last(where: {
            $0.contains("Error") || $0.contains("Exception") || $0.hasPrefix("ModuleNotFound")
        }) { return String(err.prefix(120)) }
        return lines.last(where: { !$0.hasPrefix("File \"") && !$0.hasPrefix("Traceback") })
            .map { String($0.prefix(120)) }
    }
}
