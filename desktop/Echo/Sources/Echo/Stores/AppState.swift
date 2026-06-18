import SwiftUI
import AppKit
import Observation
import EchoKit

/// 轻量 UI 打点：写 stderr（终端可见）+ 追加到 /tmp/echo-ui.log（卡死后可直接读文件定位
/// 最后触发的入口，无需手动复制终端）。交互频率低，常开无害。
@inline(never) func uiLog(_ s: String) {
    let line = "[echo-ui] " + s + "\n"
    FileHandle.standardError.write(Data(line.utf8))
    let path = "/tmp/echo-ui.log"
    if let fh = FileHandle(forWritingAtPath: path) {
        fh.seekToEndOfFile(); fh.write(Data(line.utf8)); try? fh.close()
    } else {
        try? line.data(using: .utf8)?.write(to: URL(fileURLWithPath: path))
    }
}

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
    /// 流式刷新计数：每次把累积态写进 transcript 时 +1，供滚动区跟随到底部
    /// （流式只替换同一条消息、transcript.count 不变，故不能只靠条数变化触发滚动）。
    private(set) var streamTick = 0

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
    /// 合批刷新：delta 高频到达时，最多每 ~16ms 才把累积态写进 transcript（避免逐字 diff 抖动）。
    private var flushScheduled = false
    /// 会话切换计数：每次 selectConversation +1。迟到的 resume/历史解析用它判断是否已过期。
    private(set) var historyEpoch = 0

    init() {}

    /// 接入真后端：建协调器、spawn gateway、泵事件。
    func connectLive() {
        let coord = GatewayCoordinator(app: self)
        coordinator = coord
        conversations = []
        transcript = []
        selectedConversationId = nil   // 清掉 mock 残留 "c2"，进来是干净空态
        Task { await coord.start() }
        startHeartbeat()   // 常开：仅在主线程卡顿 >700ms 时写一行，平时零噪音——抓难复现的卡死
        if ProcessInfo.processInfo.environment["ECHO_APP_SELFTEST"] == "1" {
            Task { await runSelfTest() }
        }
    }

    /// 诊断：合成一个与最大真实会话同量级的历史（30 条，含一条 ~30KB 长消息 + 代码块），
    /// 直接灌进 transcript 并选中 → 复现「打开长会话卡顿」，不依赖 gateway。ECHO_APP_FAKEBIG=1。
    func loadFakeBig() {
        startHeartbeat()
        conversations = [ConversationSummary(id: "big", title: "Big session", preview: "synthetic",
                                             timestamp: Date(timeIntervalSince1970: 0), pinned: false)]
        selectedConversationId = "big"
        // 用真实「NLP 广告」会话里 emoji 最密的那条回复（含 ZWJ 序列 🧑‍💻🏃‍♂️ + 变体选择符 ❗️✈️）
        // 复刻——这是怀疑卡顿的真凶（emoji 簇 + textSelection 的布局开销）。
        let emojiHeavy = """
        ---

        **标题：**
        别再卷传统开发了❗NLP才是AI时代的薪资天花板💰🔥

        **正文：**

        家人们听我说🗣️ 大模型时代，会调API的人满大街都是，但真正懂NLP底层的人，才是企业抢着要的稀缺人才啊❗️❗️

        ✅ 这门课到底有多香？
        🔹 Transformer原理 → 不只会调包，直接降维打击😎
        🔹 RAG/Agent实战 → 大厂最🔥技术栈
        🔹 企业级项目带你做 → 简历直接起飞✈️

        💡 适合谁？
        · 想转行AI的程序员/产品经理🧑‍💻
        · 在校生想拿大厂实习🎯
        · 在职想涨薪30%+的打工人💸

        🎁 现在冲还有早鸟专属价‼️ 名额有限先到先得🏃‍♂️💨
        """
        var items: [PreparedHistoryItem] = []
        for i in 0..<8 {
            items.append(.user("第 \(i) 轮：再活泼一点🎉"))
            items.append(.assistant(MarkdownParser.parse(emojiHeavy)))
        }
        uiLog("loadFakeBig: feeding \(items.count) emoji-heavy items (NOSEL=\(ProcessInfo.processInfo.environment["ECHO_APP_NOSEL"] ?? "0"))")
        applyPreparedHistory(items)
        // 强制窗口前置——后台启动的窗口若不是 key，SwiftUI 会推迟布局，测不到真实卡顿。
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 300_000_000)
            NSApp.activate(ignoringOtherApps: true)
            NSApp.windows.first?.makeKeyAndOrderFront(nil)
        }
    }

    private var heartbeatRunning = false
    /// 主线程卡顿探针：每 100ms 在 MainActor 上打一拍，迟到 >700ms（真 beachball 量级）才记一行。
    /// 常开、近乎零成本——用来抓难复现的卡死：卡顿时 /tmp/echo-ui.log 里 STALL 上一行即现场。
    func startHeartbeat() {
        guard !heartbeatRunning else { return }
        heartbeatRunning = true
        Task { @MainActor in
            var last = DispatchTime.now().uptimeNanoseconds
            while true {
                try? await Task.sleep(nanoseconds: 100_000_000)
                let now = DispatchTime.now().uptimeNanoseconds
                let gapMs = Double(now - last) / 1_000_000
                if gapMs > 700 { uiLog(String(format: "⚠️ MAIN STALL %.0fms（主线程卡住了）", gapMs)) }
                last = now
            }
        }
    }

    /// 无头压力自测：连上 → 反复 resume 历史会话 / 开关面板 / 发送 —— 触发潜在崩溃。
    /// 仅 ECHO_APP_SELFTEST=1 时跑。结果写 /tmp/echo-ui.log（uiLog）。
    func runSelfTest() async {
        // 给真 gateway 充足冷启动时间（重型 import + update-check 可达 1-2 分钟）。
        for _ in 0..<1200 where connection != .online { try? await Task.sleep(nanoseconds: 100_000_000) }
        uiLog("selftest: connection=\(connection) sessions=\(conversations.count)")
        try? await Task.sleep(nanoseconds: 800_000_000)

        // 定向复现：只打开某一个会话并强制前置，盯它是否卡。ECHO_APP_OPENONE=<sid 前缀>
        if let want = ProcessInfo.processInfo.environment["ECHO_APP_OPENONE"], !want.isEmpty {
            guard let id = conversations.first(where: { $0.id.hasPrefix(want) })?.id else {
                uiLog("selftest: OPENONE no match for \(want)"); return
            }
            NSApp.activate(ignoringOtherApps: true)
            if let w = NSApp.windows.first {
                w.setFrame(NSRect(x: 80, y: 80, width: 1400, height: 1000), display: true)
                w.makeKeyAndOrderFront(nil)
            }
            uiLog("selftest: OPENONE open \(id)")
            selectConversation(id)
            for _ in 0..<60 { try? await Task.sleep(nanoseconds: 200_000_000) }
            uiLog("selftest: OPENONE done transcript=\(transcript.count)")
            return
        }

        // 1) resume 历史会话——优先消息最多的（最复杂渲染路径）。
        let ids = conversations
            .sorted { $0.timestamp > $1.timestamp }
            .prefix(8).map(\.id)
        for id in ids {
            uiLog("selftest: open \(id)")
            selectConversation(id)
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            uiLog("selftest:   transcript=\(transcript.count)")
        }
        // 2) 开关 Echo 面板。
        uiLog("selftest: toggle panel on"); toggleEchoPanel(); try? await Task.sleep(nanoseconds: 500_000_000)
        uiLog("selftest: toggle panel off"); toggleEchoPanel(); try? await Task.sleep(nanoseconds: 300_000_000)
        // 3) 新会话 + 发送，等回复。
        uiLog("selftest: newConversation"); newConversation(); try? await Task.sleep(nanoseconds: 500_000_000)
        composerText = "Reply with exactly: OK"
        uiLog("selftest: send"); send()
        for i in 0..<400 {
            try? await Task.sleep(nanoseconds: 100_000_000)
            let got = transcript.reversed().compactMap { item -> String? in
                if case .assistant(let m) = item, !m.streaming {
                    return m.blocks.compactMap { if case .paragraph(let p) = $0 { return p } else { return nil } }.joined()
                }
                return nil
            }.first
            if let t = got, !t.isEmpty { uiLog("selftest: GOT REPLY text=\"\(t.prefix(40))\""); break }
            if i % 50 == 0 { uiLog("selftest: waiting… transcript=\(transcript.count) responding=\(isResponding)") }
        }
        // 4) 再 resume 一个 + 再发一条（多轮）。
        if let id = ids.first { uiLog("selftest: reopen \(id)"); selectConversation(id); try? await Task.sleep(nanoseconds: 700_000_000) }
        uiLog("selftest: DONE (no crash)")
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

    /// 评分态切换（点👍/👎、撤销、展开理由）——仅改队首 UI 态，不发反馈。
    /// 提交发生在 60s 窗口到期或「补充理由→提交」时（见 commitRating）。
    func setRatingState(_ s: RatingItem.RatingState) {
        guard !ratingQueue.isEmpty else { return }
        ratingQueue[0].state = s
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
        uiLog("selectConversation \(id)")
        selectedConversationId = id
        historyEpoch &+= 1   // 作废上一会话尚未落地的历史（迟到的 resume 不许灌进新会话）
        clarifyPrompt = nil; scopeQuestion = nil
        if let coordinator {
            transcript = []
            Task { await coordinator.openConversation(id) }
        } else {
            transcript = MockData.transcript(for: id)
            ratingQueue = MockData.ratings(for: id)
        }
    }

    func newConversation() {
        uiLog("newConversation")
        selectedConversationId = nil
        transcript = []; ratingQueue = []
        scopeQuestion = nil; clarifyPrompt = nil
        if let coordinator { Task { await coordinator.newConversation() } }
    }

    func toggleEchoPanel() {
        uiLog("toggleEchoPanel")
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
        uiLog("deleteConversation \(id)")
        conversations.removeAll { $0.id == id }
        coordinator?.deleteSession(id)   // 也删后端，否则重启会复现
        if selectedConversationId == id { newConversation() }
    }

    func send() {
        let text = composerText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        uiLog("send len=\(text.count)")
        composerText = ""
        transcript.append(.user(.init(id: UUID().uuidString, text: text)))
        if let coordinator { Task { await coordinator.submit(text) } }
    }

    func stop() {
        uiLog("stop")
        isResponding = false
        statusLine = nil
        if let coordinator { Task { await coordinator.interrupt() } }
    }

    /// 中间态：把历史消息「解析好的」形态，可跨执行器传递（MarkdownBlock/String 均 Sendable）。
    enum PreparedHistoryItem: Sendable {
        case user(String)
        case assistant([MarkdownBlock])
    }

    /// 回放 session.resume 的历史消息到 transcript。
    /// Markdown 解析（长会话可能几十条）放到后台执行器，主线程只做轻量映射+赋值，避免打开会话卡顿。
    func loadHistory(_ messages: [TranscriptMessage]) {
        if ProcessInfo.processInfo.environment["ECHO_APP_DUMPHIST"] == "1" {
            for (i, m) in messages.enumerated() {
                uiLog("HIST[\(i)] \(m.role) textLen=\(m.text?.count ?? -1) maxline=\(m.text?.split(separator: "\n").map(\.count).max() ?? 0)")
            }
        }
        // 先抽成 Sendable 的 (role,text)，再 detach 解析。
        let raw: [(isUser: Bool, text: String)] = messages.compactMap { m in
            switch m.role {
            case .user: return (true, m.text ?? "")
            case .assistant: return (false, m.text ?? "")
            case .system, .tool: return nil
            }
        }
        let epoch = historyEpoch   // 解析期间用户若切走，落地时丢弃
        Task.detached(priority: .userInitiated) { [weak self] in
            let prepared: [PreparedHistoryItem] = raw.map {
                $0.isUser ? .user($0.text) : .assistant(MarkdownParser.parse($0.text))
            }
            await self?.applyPreparedHistory(prepared, epoch: epoch)
        }
    }

    /// 在主线程把解析好的历史落成 transcript（轻量映射）。
    /// epoch 不匹配 = 用户已切到别的会话，迟到的历史直接丢弃，避免灌错会话。
    func applyPreparedHistory(_ items: [PreparedHistoryItem], epoch: Int? = nil) {
        if let epoch, epoch != historyEpoch {
            uiLog("applyPreparedHistory: stale epoch \(epoch)≠\(historyEpoch), dropped")
            return
        }
        let t0 = DispatchTime.now().uptimeNanoseconds
        defer {
            let ms = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000
            if ms > 50 { uiLog(String(format: "⚠️ applyPreparedHistory items=%d slow %.0fms", items.count, ms)) }
        }
        transcript = items.map { item in
            switch item {
            case .user(let t):
                return .user(.init(id: UUID().uuidString, text: t))
            case .assistant(let blocks):
                return .assistant(.init(id: UUID().uuidString, blocks: blocks.map(Self.mapBlock)))
            }
        }
    }

    /// Kit MarkdownBlock → UI ResponseBlock（纯映射，可在任意执行器调用）。
    nonisolated static func mapBlock(_ block: MarkdownBlock) -> ResponseBlock {
        switch block {
        case .paragraph(let p): return .paragraph(p)
        case .heading(_, let t): return .heading(t)
        case .bullets(let items): return .bullets(items)
        case .code(let lang, let body): return .code(language: lang, text: body)
        }
    }

    func applySessionList(_ items: [SessionListItem]) {
        let pinnedIds = Set(conversations.filter { $0.pinned }.map(\.id))  // 置顶是本地态，刷新时保留
        conversations = items.map { item in
            ConversationSummary(
                id: item.id,
                title: item.title.isEmpty ? String(item.preview.prefix(28)) : item.title,
                preview: item.preview,
                timestamp: Date(timeIntervalSince1970: item.startedAt),
                pinned: pinnedIds.contains(item.id)
            )
        }
    }

    /// 内联点赞（每条 agent 回复末尾的 👍/👎）：设该消息评分态 + 提交反馈（用其关联 invocation）。
    func rateMessage(_ messageId: String, thumb: Int) {
        for (i, item) in transcript.enumerated() {
            guard case .assistant(var m) = item, m.id == messageId else { continue }
            m.rating = (m.rating == thumb) ? nil : thumb   // 再点同一个 = 取消
            transcript[i] = .assistant(m)
            if let inv = m.invocationId, let r = m.rating {
                coordinator?.sendFeedback(invocationId: inv, rating: r, reason: nil)
            }
            return
        }
    }

    /// 点赞/点踩后补充理由：带 reason 再提交一次（后端 reason_score LLM 校准置信度）。
    func submitRatingReason(_ messageId: String, reason: String) {
        let cleaned = reason.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }
        for item in transcript {
            guard case .assistant(let m) = item, m.id == messageId else { continue }
            if let inv = m.invocationId, let r = m.rating {
                coordinator?.sendFeedback(invocationId: inv, rating: r, reason: cleaned)
            }
            return
        }
    }

    /// 重试：去掉这条助手回复，用它前面那条用户消息重新发一遍（重新生成）。
    func retry(_ messageId: String) {
        guard let i = transcript.firstIndex(where: {
            if case .assistant(let m) = $0 { return m.id == messageId }; return false
        }) else { return }
        let userText = transcript[..<i].reversed().compactMap { item -> String? in
            if case .user(let u) = item { return u.text } else { return nil }
        }.first
        guard let text = userText else { return }
        transcript.remove(at: i)
        if let coordinator { Task { await coordinator.submit(text) } }
    }

    /// 回复完成后把最近一次 invocation 关联到最后一条助手消息（供内联点赞提交）。
    func attachInvocationToLastMessage(_ invId: Int) {
        for i in transcript.indices.reversed() {
            guard case .assistant(var m) = transcript[i] else { continue }
            if m.invocationId == nil { m.invocationId = invId; transcript[i] = .assistant(m) }
            return
        }
    }

    /// 提交队首评分并出队：POST /feedback（thumb + 可选 reason，带 invocation_id 精确归属）。
    /// 触发点 = 60s 撤销窗到期，或用户在「补充理由」里点提交。撤销不会走到这里 → 真取消、不发反馈。
    func commitRating(thumb: Int, reason: String?) {
        guard let item = ratingQueue.first else { return }
        let cleaned = reason?.trimmingCharacters(in: .whitespacesAndNewlines)
        coordinator?.sendFeedback(invocationId: item.id, rating: thumb,
                                  reason: (cleaned?.isEmpty ?? true) ? nil : cleaned)
        ratingQueue.removeFirst()
    }

    // MARK: - Phase 3: gateway 事件 → UI 归约

    /// 把一个 gateway 事件映射成 transcript / 状态变更（在 MainActor 上调用）。
    func handle(_ event: ParsedEvent) {
        switch event.event {
        case .ready:
            uiLog("event ready → online")
            connection = .online
        case .sessionInfo:
            break // 可在此更新模型/技能元数据
        case .messageStart:
            uiLog("event messageStart")
            beginAssistantTurn()
        case .messageDelta(let d):
            streamingText += d.text
            if !isResponding { isResponding = true }   // 避免每条 delta 都触发 observable 失效
            scheduleStreamFlush()          // 合批到 ~16ms 一刷，高频流式不抖
        case .messageComplete(let c):
            uiLog("event messageComplete len=\(c.text.count)")
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
            scheduleStreamFlush()
        case .clarifyRequest(let c):
            uiLog("event clarifyRequest")
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

    /// 合批：把一帧内的多条 delta 合成一次 transcript 刷新（~16ms 节拍，对齐 Tokens.Timing.streamFlush）。
    /// 多次调用只排一个待刷任务；message.complete 把 streamingId 清空后，迟到的 flush 会自然 no-op。
    private func scheduleStreamFlush() {
        guard !flushScheduled else { return }
        flushScheduled = true
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 16_000_000)
            guard let self else { return }
            self.flushScheduled = false
            self.refreshStreamingMessage()
        }
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
        streamTick &+= 1
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
        streamTick &+= 1
        // Phase 4: 拉 /invocations/recent 显示评分
        coordinator?.refreshRatingQueue()
        // 新建会话发完首轮 → 侧栏还没有这条 → 刷新会话列表让它出现。
        if let sel = selectedConversationId, !conversations.contains(where: { $0.id == sel }) {
            coordinator?.refreshSessions()
        }
    }

    /// Kit Markdown 块 → UI ResponseBlock（流式收尾在主线程用；历史回放走 mapBlock 后台解析）。
    static func renderBlocks(from text: String) -> [ResponseBlock] {
        guard !text.isEmpty else { return [] }
        return MarkdownParser.parse(text).map(Self.mapBlock)
    }
}
