import Foundation

/// 指数退避计时器（重连用）。1→2→4→8…封顶 cap，可选抖动。
/// 纯逻辑，便于自检。
public struct ExponentialBackoff: Sendable {
    public let base: Double
    public let factor: Double
    public let cap: Double
    public let maxAttempts: Int?
    private(set) public var attempt: Int = 0

    public init(base: Double = 1, factor: Double = 2, cap: Double = 30, maxAttempts: Int? = nil) {
        self.base = base; self.factor = factor; self.cap = cap; self.maxAttempts = maxAttempts
    }

    /// 是否还应继续重试。
    public var shouldRetry: Bool {
        guard let maxAttempts else { return true }
        return attempt < maxAttempts
    }

    /// 取下一个延迟（秒）并推进计数。超过 maxAttempts 返回 nil。
    public mutating func next() -> Double? {
        if let maxAttempts, attempt >= maxAttempts { return nil }
        let delay = min(cap, base * pow(factor, Double(attempt)))
        attempt += 1
        return delay
    }

    /// 连接成功后复位。
    public mutating func reset() { attempt = 0 }
}
