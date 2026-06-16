import Foundation

/// Gateway JSON-RPC 客户端。维持一条 transport，做请求/响应配对与事件分发。
/// 设计见 DevPlan/desktop-app-dev-plan.md §6。
public actor GatewayClient {

    public enum State: Sendable, Equatable {
        case disconnected, connecting, ready, failed
    }

    public private(set) var state: State = .disconnected

    /// 单次 call 的超时（秒）。长任务如 prompt.submit 立即返回 {ok}，真正输出走事件，
    /// 所以普通 call 不该久等。
    public var callTimeout: Double = 30
    public func setCallTimeout(_ seconds: Double) { callTimeout = seconds }

    /// 调试：每收到一帧原始数据回调一次（诊断用）。
    private var onRawFrame: (@Sendable (Data) -> Void)?
    public func setRawFrameLogger(_ cb: (@Sendable (Data) -> Void)?) { onRawFrame = cb }

    private var transport: GatewayTransport?
    private var nextId = 1
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var receiveLoop: Task<Void, Never>?

    /// 事件总线，Stores 订阅。
    public nonisolated let events: AsyncStream<ParsedEvent>
    private let eventSink: AsyncStream<ParsedEvent>.Continuation

    public init() {
        var sink: AsyncStream<ParsedEvent>.Continuation!
        events = AsyncStream { sink = $0 }
        eventSink = sink
    }

    // MARK: 连接

    /// 绑定一个 transport 并启动接收循环。等待 gateway.ready 事件后返回。
    public func connect(_ transport: GatewayTransport) async {
        self.transport = transport
        state = .connecting
        receiveLoop?.cancel()
        receiveLoop = Task { [weak self] in await self?.runReceiveLoop() }
    }

    /// 便捷：用 WebSocket 连到给定 URL。
    public func connect(url: URL) async {
        await connect(WebSocketTransport(url: url))
    }

    public func disconnect() {
        receiveLoop?.cancel()
        let t = transport
        transport = nil
        state = .disconnected
        failAllPending(GatewayError.transportClosed)
        Task { await t?.close() }
    }

    // MARK: 调用

    /// 发一条 JSON-RPC 请求并等待强类型响应。
    public func call<P: Encodable & Sendable, R: Decodable & Sendable>(
        _ method: String, _ params: P, as: R.Type = R.self
    ) async throws -> R {
        guard let transport else { throw GatewayError.notConnected }
        let id = nextId; nextId += 1
        let req = RPCRequest(id: id, method: method, params: params)
        let data = try JSONEncoder().encode(req)

        let timeout = callTimeout
        let respData: Data = try await withCheckedThrowingContinuation { cont in
            pending[id] = cont
            Task {
                do { try await transport.send(data) }
                catch { await self.resumePending(id, throwing: error) }
            }
            Task {
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                await self.timeoutPending(id)
            }
        }
        let resp = try GatewayDecoder.decodeResponse(R.self, from: respData)
        if let err = resp.error { throw err }
        guard let result = resp.result else { throw GatewayError.emptyResult }
        return result
    }

    /// 无参便捷调用。
    public func call<R: Decodable & Sendable>(_ method: String, as: R.Type = R.self) async throws -> R {
        try await call(method, EmptyParams(), as: R.self)
    }

    // MARK: 接收循环

    private func runReceiveLoop() async {
        guard let transport else { return }
        while !Task.isCancelled {
            do {
                let data = try await transport.receive()
                handle(data)
            } catch {
                state = .failed
                failAllPending(error)
                return
            }
        }
    }

    private func handle(_ data: Data) {
        onRawFrame?(data)
        switch GatewayDecoder.classify(data) {
        case .response(let id):
            resumePending(id, returning: data)
        case .event(let meta):
            let parsed = EventParser.parse(meta: meta, data: data)
            if case .ready = parsed.event { state = .ready }
            eventSink.yield(parsed)
        case .unknown:
            break // 协议噪音，忽略（对照 createGatewayEventHandler.ts 的容错）
        }
    }

    // MARK: 配对管理

    private func resumePending(_ id: Int, returning data: Data) {
        if let cont = pending.removeValue(forKey: id) { cont.resume(returning: data) }
    }
    private func resumePending(_ id: Int, throwing error: Error) {
        if let cont = pending.removeValue(forKey: id) { cont.resume(throwing: error) }
    }
    private func timeoutPending(_ id: Int) {
        if let cont = pending.removeValue(forKey: id) { cont.resume(throwing: GatewayError.timeout) }
    }
    private func failAllPending(_ error: Error) {
        let conts = pending; pending.removeAll()
        for (_, c) in conts { c.resume(throwing: error) }
    }

    // 测试可见
    public var pendingCount: Int { pending.count }
    public var currentNextId: Int { nextId }
}

// MARK: - 类型化便捷封装

public extension GatewayClient {
    struct SessionIdParams: Encodable, Sendable { public let session_id: String }
    struct PromptParams: Encodable, Sendable { public let session_id: String; public let text: String }
    struct ClarifyParams: Encodable, Sendable {
        public let session_id: String; public let request_id: String; public let answer: String
    }
    struct TitleParams: Encodable, Sendable { public let session_id: String; public let title: String? }

    func listSessions() async throws -> [SessionListItem] {
        let r: SessionListResponse = try await call(GatewayMethod.sessionList)
        return r.sessions ?? []
    }
    func createSession() async throws -> SessionCreateResponse {
        try await call(GatewayMethod.sessionCreate)
    }
    func resumeSession(_ id: String) async throws -> SessionResumeResponse {
        try await call(GatewayMethod.sessionResume, SessionIdParams(session_id: id))
    }
    @discardableResult
    func submitPrompt(session: String, text: String) async throws -> PromptSubmitResponse {
        try await call(GatewayMethod.promptSubmit, PromptParams(session_id: session, text: text))
    }
    @discardableResult
    func interrupt(session: String) async throws -> OKResponse {
        try await call(GatewayMethod.sessionInterrupt, SessionIdParams(session_id: session))
    }
    @discardableResult
    func respondClarify(session: String, requestId: String, answer: String) async throws -> OKResponse {
        try await call(GatewayMethod.clarifyRespond,
                       ClarifyParams(session_id: session, request_id: requestId, answer: answer))
    }
    @discardableResult
    func deleteSession(_ id: String) async throws -> SessionDeleteResponse {
        try await call(GatewayMethod.sessionDelete, SessionIdParams(session_id: id))
    }
}
