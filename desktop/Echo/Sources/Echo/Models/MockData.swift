import Foundation

// Phase 1 的 mock 数据，用于 #Preview 与无后端走查。内容呼应参考截图，
// 但全部为占位文本，不含任何外部受版权资料。

enum MockData {

    static func minutesAgo(_ m: Int) -> Date { Date(timeIntervalSinceNow: -Double(m) * 60) }
    static func daysAgo(_ d: Int) -> Date { Date(timeIntervalSinceNow: -Double(d) * 86_400) }

    static let conversations: [ConversationSummary] = [
        .init(id: "c1", title: "Healthy 30-Minute Recipes",
              preview: "You can prepare a variety of healthy and satisfying meals in under 30 minutes by focusing on…",
              timestamp: daysAgo(2), pinned: true,
              thumbnailSymbol: "fork.knife", thumbnailTint: .warm),
        .init(id: "c2", title: "Largest City Park",
              preview: "The largest urban park spans well over a thousand acres and is one of the largest in the hemisphere.",
              timestamp: minutesAgo(3),
              thumbnailSymbol: "tree.fill", thumbnailTint: .cool),
        .init(id: "c3", title: "Social Media Launch Email",
              preview: "Here are a few ways you can present solutions to the team, depending on how you'd prefer to…",
              timestamp: minutesAgo(17)),
        .init(id: "c4", title: "History of Motion Pictures",
              preview: "Early motion picture experiments date back to the late nineteenth century with rapid sequential…",
              timestamp: daysAgo(1),
              thumbnailSymbol: "film.fill", thumbnailTint: .mono),
        .init(id: "c5", title: "Chanterelle Mushrooms",
              preview: "Chanterelles are prized wild mushrooms known for their fruity aroma and golden, funnel shape.",
              timestamp: minutesAgo(94),
              thumbnailSymbol: "leaf.fill", thumbnailTint: .warm),
        .init(id: "c6", title: "Rarest Pigment Explanation",
              preview: "Some pigments were historically so costly they were reserved for the most important works.",
              timestamp: daysAgo(1),
              thumbnailSymbol: "paintpalette.fill", thumbnailTint: .accent),
        .init(id: "c7", title: "Simple Daily Skincare",
              preview: "A minimal routine focused on cleansing, moisturizing, and sun protection covers the essentials.",
              timestamp: daysAgo(1)),
        .init(id: "c8", title: "Distributed Service Diagram",
              preview: "画个分布式服务的架构示意：网关、服务发现、消息队列、数据层分层展开。",
              timestamp: minutesAgo(40),
              thumbnailSymbol: "square.grid.3x3.fill", thumbnailTint: .cool),
    ]

    /// 对照截图主对话区的一轮问答（占位文本）。
    static var sampleTranscript: [TranscriptItem] {
        [
            .user(.init(id: "m1", text: "What's the largest park in this city?")),
            .assistant(.init(
                id: "m2",
                blocks: [
                    .paragraph("The largest park here covers well over a thousand acres, making it one of the largest city parks in the region."),
                    .image(.init(id: "im1", symbol: "photo.artframe", caption: nil, tint: .warm)),
                    .heading("A Sprawling Urban Forest"),
                    .paragraph("Often called the \"lungs\" of the city, this ecological space centers on a hill and divides into several sections. It is home to numerous attractions, including:"),
                    .bullets([
                        "A historic castle",
                        "A major museum of anthropology",
                        "A city zoo",
                        "Several lakes and cultural centers",
                    ]),
                    .paragraph("It is considered one of the world's most visited urban parks."),
                ],
                toolActivities: [
                    .init(id: "t1", name: "web_search", preview: "largest city park acreage",
                          state: .done, durationS: 0.8, summary: "3 sources"),
                ],
                reasoning: "Cross-checked acreage figures across a few sources and picked the most consistent description.",
                sources: ["Encyclopedia", "+2"],
                usage: .init(durationS: 2.1, tokens: 1200, model: "qwen-plus"),
                invocationId: 142,
                skillName: "research-summary"
            )),
        ]
    }

    static var sampleRatings: [RatingItem] {
        [.init(id: 142, skillName: "research-summary", state: .idle)]
    }

    static var sampleScope: ScopeQuestion {
        .init(id: "rename-batch", skillName: "Batch Rename")
    }

    static var sampleClarify: ClarifyPrompt {
        .init(id: "req42",
              question: "你这套「批量重命名 + 提交」的做法，要不要存成一个可复用技能？",
              choices: ["好，存为技能", "不用了", "这次不要"])
    }
}
