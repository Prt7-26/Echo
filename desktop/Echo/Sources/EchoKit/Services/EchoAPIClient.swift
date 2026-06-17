import Foundation

/// Hermes dashboard 的临时会话鉴权。dashboard 给所有 `/api/*` 强制 `X-Hermes-Session-Token`
/// （`secrets.token_urlsafe(32)`，每次启动随机、内存里），并把它注入在公开根页 `/` 的
/// `<script>window.__HERMES_SESSION_TOKEN__="…"` 里。原生 App 不在那个页面里跑、拿不到
/// cookie/注入值，所以这里主动抓根页、刮出 token、缓存；401 时失效重取。零改 Hermes。
public actor DashboardAuth {
    public static let headerName = "X-Hermes-Session-Token"

    private let origin: URL    // http://127.0.0.1:9119
    private let fetch: @Sendable (URLRequest) async throws -> (Data, HTTPURLResponse)
    private var cached: String?

    public init(origin: URL, session: URLSession = .shared) {
        self.origin = origin
        self.fetch = { req in
            let (d, r) = try await session.data(for: req)
            guard let http = r as? HTTPURLResponse else { throw EchoAPIError.nonHTTPResponse }
            return (d, http)
        }
    }

    /// 测试构造：注入 fetch。
    public init(origin: URL, fetch: @escaping @Sendable (URLRequest) async throws -> (Data, HTTPURLResponse)) {
        self.origin = origin
        self.fetch = fetch
    }

    public func invalidate() { cached = nil }

    /// 返回缓存或现抓的 token（抓不到返回 nil，调用方按无鉴权继续——失败软化）。
    public func token() async -> String? {
        if let cached { return cached }
        var req = URLRequest(url: origin.appendingPathComponent("/"))
        req.timeoutInterval = 6
        guard let (data, http) = try? await fetch(req), (200..<300).contains(http.statusCode) else {
            return nil
        }
        let html = String(decoding: data, as: UTF8.self)
        let tok = Self.scrapeToken(html)
        cached = tok
        return tok
    }

    /// 从 index.html 刮出 window.__HERMES_SESSION_TOKEN__="…"。
    static func scrapeToken(_ html: String) -> String? {
        let marker = "window.__HERMES_SESSION_TOKEN__=\""
        guard let r = html.range(of: marker) else { return nil }
        let rest = html[r.upperBound...]
        guard let end = rest.firstIndex(of: "\"") else { return nil }
        let tok = String(rest[..<end])
        return tok.isEmpty ? nil : tok
    }
}

/// Echo 信号 REST 客户端。base = http://host:port/api/plugins/echo_signals
/// 端点见 plugins/echo_signals/dashboard/plugin_api.py 与 DevPlan §3.4。
public struct EchoAPIClient: Sendable {

    public typealias Fetch = @Sendable (URLRequest) async throws -> (Data, HTTPURLResponse)

    public let base: URL
    private let fetch: Fetch
    /// dashboard 鉴权（生产构造自动从 base 推导 origin 启用；测试构造为 nil = 无鉴权）。
    private let auth: DashboardAuth?
    /// 单次请求超时（秒）。dashboard 没起/无响应时不让信号调用拖住 App。
    private let timeout: Double

    /// 生产构造：走 URLSession，自动启用 dashboard 鉴权 + 超时。
    public init(base: URL, session: URLSession = .shared, timeout: Double = 8) {
        self.base = base
        self.timeout = timeout
        self.auth = Self.deriveOrigin(from: base).map { DashboardAuth(origin: $0, session: session) }
        self.fetch = { req in
            let (d, r) = try await session.data(for: req)
            guard let http = r as? HTTPURLResponse else {
                throw EchoAPIError.nonHTTPResponse
            }
            return (d, http)
        }
    }

    /// 测试构造：注入 fetch，验证请求构造而不触网（无鉴权、无超时副作用）。
    public init(base: URL, fetch: @escaping Fetch) {
        self.base = base
        self.fetch = fetch
        self.auth = nil
        self.timeout = 8
    }

    /// 从 …/api/plugins/echo_signals 推导出 dashboard origin（http://host:port）。
    static func deriveOrigin(from base: URL) -> URL? {
        guard var comps = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        comps.path = ""; comps.query = nil; comps.fragment = nil
        return comps.url
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
        req.timeoutInterval = timeout
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return req
    }

    /// 带鉴权头 + 401 失效重试的 fetch。auth 为 nil（测试）时退化为直接 fetch。
    private func fetchAuthorized(_ req: URLRequest) async throws -> (Data, HTTPURLResponse) {
        guard let auth else { return try await fetch(req) }
        var r = req
        if let t = await auth.token() { r.setValue(t, forHTTPHeaderField: DashboardAuth.headerName) }
        let (d, http) = try await fetch(r)
        if http.statusCode == 401 {
            // token 过期（dashboard 重启换了 token）→ 失效重取一次。
            await auth.invalidate()
            var r2 = req
            if let t2 = await auth.token() { r2.setValue(t2, forHTTPHeaderField: DashboardAuth.headerName) }
            return try await fetch(r2)
        }
        return (d, http)
    }

    private func get<R: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> R {
        let (data, http) = try await fetchAuthorized(makeRequest(path, query: query))
        try Self.checkStatus(http)
        return try GatewayDecoder.json.decode(R.self, from: data)
    }

    private func postVoid<B: Encodable>(_ path: String, body: B) async throws {
        let data = try JSONEncoder().encode(body)
        try await sendVoid(makeRequest(path, method: "POST", body: data))
    }

    private func sendVoid(_ req: URLRequest) async throws {
        let (_, http) = try await fetchAuthorized(req)
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
