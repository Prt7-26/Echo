import Foundation

// JSON-RPC 2.0 信封。线协议见 DevPlan/desktop-app-dev-plan.md §3.1。
// 请求:  {"jsonrpc":"2.0","id":N,"method":"<verb>","params":{...}}
// 响应:  {"jsonrpc":"2.0","id":N,"result":{...}}  |  {"jsonrpc":"2.0","id":N,"error":{...}}
// 事件:  {"jsonrpc":"2.0","method":"event","params":{"event":"<name>","sid":"...",...payload}}

// MARK: - 出站请求

public struct RPCRequest<P: Encodable>: Encodable {
    public let jsonrpc = "2.0"
    public let id: Int
    public let method: String
    public let params: P
    public init(id: Int, method: String, params: P) {
        self.id = id; self.method = method; self.params = params
    }
    private enum CodingKeys: String, CodingKey { case jsonrpc, id, method, params }
}

/// 无参请求的占位 params。
public struct EmptyParams: Encodable, Sendable { public init() {} }

// MARK: - 入站响应

public struct RPCError: Decodable, Error, Equatable {
    public let code: Int
    public let message: String
}

public struct RPCResponse<R: Decodable>: Decodable {
    public let id: Int?
    public let result: R?
    public let error: RPCError?
}

// MARK: - 帧头（先解 method/id 决定路由）

/// 轻量帧头：只读 id / method，用来判定「响应 vs 事件」。
public struct FrameHead: Decodable {
    public let id: Int?
    public let method: String?
}

/// 事件元信息。真实帧形状（实读 server.py:_emit）：
///   params = {"type": <事件名>, "session_id": <sid>, "session_key": <hermes id>, "payload": {...}}
/// 注意：事件名在 `type`，payload **嵌套**在 `payload` 键下（不是兄弟键）。
/// `session_key` 是 Echo 的扩展——评分 widget 要用它而非 gateway 内部 sid 来定位会话。
public struct EventMeta: Decodable {
    public let event: String          // params.type
    public let sid: String?           // params.session_id
    public let sessionKey: String?    // params.session_key (Echo addition)

    private enum CodingKeys: String, CodingKey {
        case event = "type"
        case sid = "session_id"
        case sessionKey = "session_key"
    }
}

private struct EventMetaFrame: Decodable {
    let params: EventMeta
}

private struct PayloadFrame<P: Decodable>: Decodable {
    struct Params: Decodable { let payload: P? }
    let params: Params
}

// MARK: - 解码入口

public enum GatewayDecoder {
    public static let json: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()

    /// 判定一帧是响应还是事件。
    public enum Frame {
        case response(id: Int)
        case event(EventMeta)
        case unknown
    }

    public static func classify(_ data: Data) -> Frame {
        guard let head = try? json.decode(FrameHead.self, from: data) else { return .unknown }
        if head.method == "event" {
            if let meta = try? json.decode(EventMetaFrame.self, from: data) {
                return .event(meta.params)
            }
            return .unknown
        }
        if let id = head.id { return .response(id: id) }
        return .unknown
    }

    /// 把一帧解成强类型响应。
    public static func decodeResponse<R: Decodable>(_ type: R.Type, from data: Data) throws -> RPCResponse<R> {
        try json.decode(RPCResponse<R>.self, from: data)
    }

    /// 把事件帧的 payload 解成强类型。payload 嵌套在 `params.payload`；
    /// 无 payload 键（如 reasoning.available）时返回 nil。
    public static func decodeEventPayload<P: Decodable>(_ type: P.Type, from data: Data) throws -> P? {
        try json.decode(PayloadFrame<P>.self, from: data).params.payload
    }
}
