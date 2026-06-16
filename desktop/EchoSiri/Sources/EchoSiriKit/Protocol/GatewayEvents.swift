import Foundation

// Gateway 事件 payload。字段对照 tui_gateway/server.py 的 _emit(...) 与
// ui-tui/src/app/createGatewayEventHandler.ts。事件名见 GatewayEventName。

/// 已知事件名常量（params.event 的取值）。
public enum GatewayEventName {
    public static let ready = "gateway.ready"
    public static let sessionInfo = "session.info"
    public static let messageStart = "message.start"
    public static let messageDelta = "message.delta"
    public static let messageComplete = "message.complete"
    public static let toolGenerating = "tool.generating"
    public static let toolProgress = "tool.progress"
    public static let toolComplete = "tool.complete"
    public static let reasoningDelta = "reasoning.delta"
    public static let thinkingDelta = "thinking.delta"
    public static let reasoningAvailable = "reasoning.available"
    public static let statusUpdate = "status.update"
    public static let clarifyRequest = "clarify.request"
    public static let approvalRequest = "approval.request"
    public static let sudoRequest = "sudo.request"
    public static let secretRequest = "secret.request"
    public static let error = "error"
    public static let skinChanged = "skin.changed"
    public static let voiceTranscript = "voice.transcript"
    public static let voiceStatus = "voice.status"
    public static let browserProgress = "browser.progress"
}

// MARK: - 流式回复

public struct MessageDelta: Decodable, Sendable {
    public let text: String
    public let rendered: String?
}

public struct MessageComplete: Decodable, Sendable {
    public let text: String
    public let usage: Usage?
    public let status: String?
    public let reasoning: String?
    public let warning: String?
    public let rendered: String?
}

// MARK: - 工具 / 推理

public struct ToolGenerating: Decodable, Sendable {
    public let name: String
}

public struct ToolProgress: Decodable, Sendable {
    public let name: String?
    public let preview: String?
}

public struct ToolComplete: Decodable, Sendable {
    public let toolId: String?
    public let name: String?
    public let error: String?
    public let summary: String?
    public let durationS: Double?
    private enum CodingKeys: String, CodingKey {
        case name, error, summary
        case toolId = "tool_id"
        case durationS = "duration_s"
    }
}

public struct TextDelta: Decodable, Sendable {
    public let text: String
}

public struct StatusUpdate: Decodable, Sendable {
    public let kind: String?
    public let text: String?
}

// MARK: - 交互请求（审批 / 澄清 / 凭据）

/// clarify.request —— Echo M1 主动提名的关键链路。
public struct ClarifyRequest: Decodable, Sendable, Equatable {
    public let question: String
    public let choices: [String]
    public let requestId: String
    private enum CodingKeys: String, CodingKey { case question, choices, requestId = "request_id" }
}

public struct ApprovalRequest: Decodable, Sendable {
    public let command: String?
    public let description: String?
    public let requestId: String?
    private enum CodingKeys: String, CodingKey { case command, description, requestId = "request_id" }
}

public struct SecretRequest: Decodable, Sendable {
    public let envVar: String?
    public let prompt: String?
    public let requestId: String?
    private enum CodingKeys: String, CodingKey { case envVar = "env_var", prompt, requestId = "request_id" }
}

public struct ErrorEvent: Decodable, Sendable {
    public let text: String?
    public let message: String?
    /// 取可用的那条文案。
    public var displayText: String { text ?? message ?? "unknown error" }
}

// MARK: - 类型化事件（GatewayClient 派发用）

/// 解析后的强类型事件。未知事件落 `.other` 保留原名，不丢帧。
public enum GatewayEvent: Sendable {
    case ready
    case sessionInfo(SessionInfo)
    case messageStart
    case messageDelta(MessageDelta)
    case messageComplete(MessageComplete)
    case toolGenerating(ToolGenerating)
    case toolProgress(ToolProgress)
    case toolComplete(ToolComplete)
    case reasoningDelta(TextDelta)
    case thinkingDelta(TextDelta)
    case reasoningAvailable
    case statusUpdate(StatusUpdate)
    case clarifyRequest(ClarifyRequest)
    case approvalRequest(ApprovalRequest)
    case secretRequest(SecretRequest)
    case error(ErrorEvent)
    case other(name: String)
}

/// 事件 + 其会话 id 的组合，投递到 Stores。
public struct ParsedEvent: Sendable {
    public let sid: String?
    public let event: GatewayEvent
    public init(sid: String?, event: GatewayEvent) { self.sid = sid; self.event = event }
}
