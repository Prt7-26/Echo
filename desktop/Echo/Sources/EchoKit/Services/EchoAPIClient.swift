import Foundation

/// Echo 信号 REST 客户端。base = http://host:port/api/plugins/echo_signals
/// 端点见 plugins/echo_signals/dashboard/plugin_api.py 与 DevPlan §3.4。
public struct EchoAPIClient: Sendable {

    public typealias Fetch = @Sendable (URLRequest) async throws -> (Data, HTTPURLResponse)

    public let base: URL
    private let fetch: Fetch

    /// 生产构造：走 URLSession。
    public init(base: URL, session: URLSession = .shared) {
        self.base = base
        self.fetch = { req in
            let (d, r) = try await session.data(for: req)
            guard let http = r as? HTTPURLResponse else {
                throw EchoAPIError.nonHTTPResponse
            }
            return (d, http)
        }
    }

    /// 测试构造：注入 fetch，验证请求构造而不触网。
    public init(base: URL, fetch: @escaping Fetch) {
        self.base = base
        self.fetch = fetch
    }

    // MARK: 读

    public func recentInvocations(sessionId: String? = nil, limit: Int = 5) async throws -> [EchoInvocation] {
        try await get("/invocations/recent", query: queryItems(sessionId: sessionId, limit: limit))
    }
    public func pendingScope(sessionId: String? = nil) async throws -> [ScopePending] {
        try await get("/scope/pending", query: queryItems(sessionId: sessionId))
    }
    public func candidates(limit: Int = 20, minScore: Int = 30) async throws -> [EchoCandidate] {
        try await get("/candidates", query: [.init(name: "limit", value: "\(limit)"),
                                             .init(name: "min_score", value: "\(minScore)")])
    }
    public func sessionCandidates() async throws -> [EchoCandidate] {
        try await get("/candidates/sessions")
    }
    public func preferences() async throws -> [Preference] {
        try await get("/preferences")
    }
    public func skills() async throws -> [SkillConfidence] {
        try await get("/skills")
    }
    public func status() async throws -> EchoStatus {
        try await get("/status")
    }

    // MARK: 写

    public func sendFeedback(_ body: FeedbackBody) async throws {
        try await postVoid("/feedback", body: body)
    }
    public func submitScope(_ body: ScopeBody) async throws {
        try await postVoid("/scope", body: body)
    }
    public func deletePreference(_ id: Int) async throws {
        try await sendVoid(makeRequest("/preferences/\(id)", method: "DELETE"))
    }
    public func clipboardSignal(_ body: ClipboardSignalBody) async throws {
        try await postVoid("/clipboard-signal", body: body)
    }

    // MARK: - 内部

    func queryItems(sessionId: String?, limit: Int? = nil) -> [URLQueryItem] {
        var items: [URLQueryItem] = []
        if let sessionId { items.append(.init(name: "session_id", value: sessionId)) }
        if let limit { items.append(.init(name: "limit", value: "\(limit)")) }
        return items
    }

    /// 构造一个请求（暴露给自检验证 URL/方法/body）。
    public func makeRequest(_ path: String, method: String = "GET",
                            query: [URLQueryItem] = [], body: Data? = nil) -> URLRequest {
        var comps = URLComponents(url: base.appendingPathComponent(path),
                                  resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query }
        var req = URLRequest(url: comps.url!)
        req.httpMethod = method
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    private func get<R: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> R {
        let (data, http) = try await fetch(makeRequest(path, query: query))
        try Self.checkStatus(http)
        return try GatewayDecoder.json.decode(R.self, from: data)
    }

    private func postVoid<B: Encodable>(_ path: String, body: B) async throws {
        let data = try JSONEncoder().encode(body)
        try await sendVoid(makeRequest(path, method: "POST", body: data))
    }

    private func sendVoid(_ req: URLRequest) async throws {
        let (_, http) = try await fetch(req)
        try Self.checkStatus(http)
    }

    static func checkStatus(_ http: HTTPURLResponse) throws {
        guard (200..<300).contains(http.statusCode) else {
            throw EchoAPIError.http(status: http.statusCode)
        }
    }
}

public enum EchoAPIError: Error, Equatable {
    case nonHTTPResponse
    case http(status: Int)
}
