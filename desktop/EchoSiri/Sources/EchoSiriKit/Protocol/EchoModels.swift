import Foundation

// Echo 信号 REST 模型。端点见 plugins/echo_signals/dashboard/plugin_api.py
// base = /api/plugins/echo_signals

/// 评分 widget 队列项（GET /invocations/recent）。
public struct EchoInvocation: Decodable, Identifiable, Sendable, Equatable {
    public let id: Int
    public let skillId: String?
    public let skillName: String?
    public let rated: Bool?
    public let sessionId: String?
    private enum CodingKeys: String, CodingKey {
        case id, rated
        case skillId = "skill_id"
        case skillName = "skill_name"
        case sessionId = "session_id"
    }
}

/// M2 待回答的 scope 问题（GET /scope/pending）。
public struct ScopePending: Decodable, Identifiable, Sendable, Equatable {
    public var id: String { skillId }
    public let skillId: String
    public let skillName: String?
    public let sessionId: String?
    private enum CodingKeys: String, CodingKey {
        case skillId = "skill_id"
        case skillName = "skill_name"
        case sessionId = "session_id"
    }
}

/// M1 候选（GET /candidates 与 /candidates/sessions）。
public struct EchoCandidate: Decodable, Identifiable, Sendable, Equatable {
    public let id: Int
    public let score: Int
    public let reasons: [String]?
}

/// M5 偏好库一项（GET /preferences）。
public struct Preference: Decodable, Identifiable, Sendable, Equatable {
    public let id: Int
    public let userMessage: String?
    public let compositeScore: Double?
    public let useCount: Int?
    private enum CodingKeys: String, CodingKey {
        case id
        case userMessage = "user_message"
        case compositeScore = "composite_score"
        case useCount = "use_count"
    }
}

/// M4 置信度排名一项（GET /skills）。
public struct SkillConfidence: Decodable, Identifiable, Sendable, Equatable {
    public var id: String { skillId }
    public let skillId: String
    public let skillName: String?
    public let confidence: Double
    public let status: String?
    public let nSignals: Int?
    private enum CodingKeys: String, CodingKey {
        case confidence, status
        case skillId = "skill_id"
        case skillName = "skill_name"
        case nSignals = "n_signals"
    }
}

/// GET /status。
public struct EchoStatus: Decodable, Sendable, Equatable {
    public let schemaVersion: Int?
    public let encoder: String?
    public let tableRows: [String: Int]?
    private enum CodingKeys: String, CodingKey {
        case encoder
        case schemaVersion = "schema_version"
        case tableRows = "table_rows"
    }
}

// MARK: - 出站 body

public struct FeedbackBody: Encodable, Sendable {
    public let invocationId: Int
    public let rating: Int
    public let reason: String?
    public let sessionId: String?
    public init(invocationId: Int, rating: Int, reason: String? = nil, sessionId: String? = nil) {
        self.invocationId = invocationId; self.rating = rating
        self.reason = reason; self.sessionId = sessionId
    }
    private enum CodingKeys: String, CodingKey {
        case rating, reason
        case invocationId = "invocation_id"
        case sessionId = "session_id"
    }
}

public struct ScopeBody: Encodable, Sendable {
    public let skillId: String
    public let level: String     // "specific" (整套) | "general" (大致想法)
    public let sessionId: String?
    public init(skillId: String, level: String, sessionId: String? = nil) {
        self.skillId = skillId; self.level = level; self.sessionId = sessionId
    }
    private enum CodingKeys: String, CodingKey {
        case level
        case skillId = "skill_id"
        case sessionId = "session_id"
    }
}

public struct ClipboardSignalBody: Encodable, Sendable {
    public let kind: String          // clipboard_copy | window_focus | window_blur
    public let length: Int?
    public let preview: String?
    public init(kind: String, length: Int? = nil, preview: String? = nil) {
        self.kind = kind; self.length = length; self.preview = preview
    }
}
