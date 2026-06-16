import Foundation

// UI 层视图模型（与 EchoSiriKit 的协议 Codable 模型分离）。
// Phase 1 用 mock 填充；Phase 3 由 gateway 事件流映射而来。

/// 侧栏画廊一张卡片。
struct ConversationSummary: Identifiable, Hashable {
    let id: String
    var title: String
    var preview: String
    var timestamp: Date
    var pinned: Bool = false
    /// 缩略图占位：mock 用 SF Symbol；Phase 3 换成真实附件图。
    var thumbnailSymbol: String? = nil
    var thumbnailTint: ThumbTint? = nil

    enum ThumbTint: Hashable { case warm, cool, mono, accent }
}

/// transcript 里的一项（一轮的用户消息或助手回复）。
enum TranscriptItem: Identifiable {
    case user(UserMessage)
    case assistant(AssistantMessage)

    var id: String {
        switch self {
        case .user(let m): return "u-\(m.id)"
        case .assistant(let m): return "a-\(m.id)"
        }
    }
}

struct UserMessage: Identifiable, Hashable {
    let id: String
    var text: String
}

/// 助手回复：富文本块 + 工具活动 + 推理 + 来源 + 用量。
struct AssistantMessage: Identifiable {
    let id: String
    var blocks: [ResponseBlock] = []
    var toolActivities: [ToolActivity] = []
    var reasoning: String? = nil
    var sources: [String] = []
    var usage: UsageLite? = nil
    /// 流式状态：true 时显示打字光标。
    var streaming: Bool = false
    /// 这一轮关联的 Echo invocation（评分用）。
    var invocationId: Int? = nil
    var skillName: String? = nil
}

/// 富文本块（对照线框图 W3）。
enum ResponseBlock: Identifiable {
    case paragraph(String)
    case heading(String)        // New York 衬线大标题
    case bullets([String])
    case image(ImageBlock)
    case code(language: String, text: String)

    var id: String {
        switch self {
        case .paragraph(let s): return "p:\(s.prefix(16))\(s.count)"
        case .heading(let s): return "h:\(s)"
        case .bullets(let b): return "b:\(b.count):\(b.first ?? "")"
        case .image(let i): return "img:\(i.id)"
        case .code(_, let t): return "code:\(t.prefix(12))\(t.count)"
        }
    }
}

struct ImageBlock: Identifiable, Hashable {
    let id: String
    /// mock: SF Symbol 占位；Phase 3 换成 URL/Data。
    var symbol: String
    var caption: String? = nil
    var tint: ConversationSummary.ThumbTint = .warm
}

/// 工具活动行（对照线框图 W4）。
struct ToolActivity: Identifiable, Hashable {
    let id: String
    var name: String
    var preview: String? = nil
    var state: State = .running
    var durationS: Double? = nil
    var summary: String? = nil

    enum State: Hashable { case running, done, failed }
}

struct UsageLite: Hashable {
    var durationS: Double?
    var tokens: Int?
    var model: String?
}

// MARK: - Echo 信号 UI 模型

/// 评分队列项。
struct RatingItem: Identifiable, Hashable {
    let id: Int            // invocation id
    var skillName: String
    var state: RatingState = .idle

    enum RatingState: Hashable {
        case idle
        case rated(thumb: Int)   // +1 / -1, 进入 60s 撤销窗
        case reason(thumb: Int)  // 展开补充理由
    }
}

/// M2 scope 问题。
struct ScopeQuestion: Identifiable, Hashable {
    let id: String         // skill id
    var skillName: String
}

/// M1 clarify 提名卡。
struct ClarifyPrompt: Identifiable, Hashable {
    let id: String         // request id
    var question: String
    var choices: [String]
}
