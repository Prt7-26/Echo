import Foundation

// 会话相关模型。字段对照 ui-tui/src/gatewayTypes.ts 与 types.ts。

public enum Role: String, Codable, Sendable {
    case user, assistant, system, tool
}

/// 侧栏画廊一张卡片的数据源（session.list 的一项）。
public struct SessionListItem: Decodable, Identifiable, Sendable, Equatable {
    public let id: String
    public let title: String
    public let preview: String
    public let messageCount: Int
    public let startedAt: Double
    public let source: String?

    private enum CodingKeys: String, CodingKey {
        case id, title, preview, source
        case messageCount = "message_count"
        case startedAt = "started_at"
    }
}

/// transcript 历史的一条消息（session.resume 回放）。
public struct TranscriptMessage: Decodable, Sendable, Equatable {
    public let role: Role
    public let text: String?
    public let name: String?
    public let context: String?
}

/// 用量。
public struct Usage: Decodable, Sendable, Equatable {
    public let input: Int
    public let output: Int
    public let total: Int
    public let calls: Int
    public let costUsd: Double?
    public let costStatus: String?
    public let contextPercent: Double?
    public let contextUsed: Int?
    public let contextMax: Int?
    public let model: String?

    private enum CodingKeys: String, CodingKey {
        case input, output, total, calls, model
        case costUsd = "cost_usd"
        case costStatus = "cost_status"
        case contextPercent = "context_percent"
        case contextUsed = "context_used"
        case contextMax = "context_max"
    }
}

/// 会话元信息（session.info 事件 + create/resume 响应内嵌）。
public struct SessionInfo: Decodable, Sendable, Equatable {
    public let model: String
    public let skills: [String: [String]]
    public let tools: [String: [String]]
    public let usage: Usage?
    public let version: String?
    public let cwd: String?
    public let profileName: String?
    public let reasoningEffort: String?
    public let fast: Bool?

    private enum CodingKeys: String, CodingKey {
        case model, skills, tools, usage, version, cwd, fast
        case profileName = "profile_name"
        case reasoningEffort = "reasoning_effort"
    }
}

// MARK: - 方法响应（节选，按需扩展）

public struct SessionCreateResponse: Decodable, Sendable {
    public let sessionId: String
    public let info: SessionInfo?
    private enum CodingKeys: String, CodingKey { case sessionId = "session_id", info }
}

public struct SessionResumeResponse: Decodable, Sendable {
    public let sessionId: String
    public let messages: [TranscriptMessage]
    public let messageCount: Int?
    public let info: SessionInfo?
    public let resumed: String?
    private enum CodingKeys: String, CodingKey {
        case sessionId = "session_id", messages, info, resumed
        case messageCount = "message_count"
    }
}

public struct SessionListResponse: Decodable, Sendable {
    public let sessions: [SessionListItem]?
}

public struct SessionTitleResponse: Decodable, Sendable {
    public let title: String?
    public let pending: Bool?
    public let sessionKey: String?
    private enum CodingKeys: String, CodingKey { case title, pending, sessionKey = "session_key" }
}

public struct SessionDeleteResponse: Decodable, Sendable {
    public let deleted: String
}

public struct OKResponse: Decodable, Sendable {
    public let ok: Bool?
}

public struct PromptSubmitResponse: Decodable, Sendable {
    public let ok: Bool?
}
