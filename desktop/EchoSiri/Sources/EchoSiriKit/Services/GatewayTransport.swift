import Foundation

/// 双向帧传输抽象。GatewayClient 不关心底层是 WebSocket 还是 stdio 还是 mock。
/// 帧 = 一条完整 JSON 对象的 UTF-8 字节（WS 每帧已自然分隔）。
public protocol GatewayTransport: Sendable {
    func send(_ data: Data) async throws
    /// 阻塞直到下一帧到达；连接关闭时抛 GatewayError.transportClosed。
    func receive() async throws -> Data
    func close() async
}

// MARK: - WebSocket 实现

/// URLSessionWebSocketTask 包装。生产路径：ws://127.0.0.1:9119/api/ws
public final class WebSocketTransport: GatewayTransport, @unchecked Sendable {
    private let task: URLSessionWebSocketTask

    public init(url: URL, session: URLSession = .shared) {
        task = session.webSocketTask(with: url)
        task.resume()
    }

    public func send(_ data: Data) async throws {
        try await task.send(.data(data))
    }

    public func receive() async throws -> Data {
        let msg = try await task.receive()
        switch msg {
        case .data(let d): return d
        case .string(let s): return Data(s.utf8)
        @unknown default: return Data()
        }
    }

    public func close() async {
        task.cancel(with: .normalClosure, reason: nil)
    }
}

// MARK: - Mock 实现（自检 / SwiftUI 预览）

/// 测试用 transport：脚本化注入入站帧、记录出站帧。
public actor MockGatewayTransport: GatewayTransport {
    private var inbound: [Data] = []
    private var sent: [Data] = []
    private var waiters: [CheckedContinuation<Data, Error>] = []
    private var closed = false

    public init() {}

    public func send(_ data: Data) async throws {
        if closed { throw GatewayError.transportClosed }
        sent.append(data)
    }

    public func receive() async throws -> Data {
        if let next = inbound.first {
            inbound.removeFirst()
            return next
        }
        if closed { throw GatewayError.transportClosed }
        return try await withCheckedThrowingContinuation { waiters.append($0) }
    }

    public func close() async {
        closed = true
        let pending = waiters; waiters.removeAll()
        for w in pending { w.resume(throwing: GatewayError.transportClosed) }
    }

    // 测试钩子 -----------------------------------------------------------

    /// 注入一帧入站数据（满足等待中的 receive，否则入队）。
    public func inject(_ data: Data) {
        if let w = waiters.first {
            waiters.removeFirst()
            w.resume(returning: data)
        } else {
            inbound.append(data)
        }
    }

    public func injectString(_ s: String) { inject(Data(s.utf8)) }

    /// 取出已发送帧（出站快照）。
    public func sentFrames() -> [Data] { sent }
}

// 非隔离的便捷扩展，nonisolated 调用需 await
public extension MockGatewayTransport {
    func lastSentString() async -> String? {
        sent.last.map { String(decoding: $0, as: UTF8.self) }
    }
}
