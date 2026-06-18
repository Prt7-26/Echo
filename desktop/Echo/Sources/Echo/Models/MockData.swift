import Foundation

// Phase 1 的 mock 数据，用于 #Preview 与无后端走查（`./echo app --mock`）。
// 内容全部为占位文本，不含任何外部受版权资料。图片用渐变 + SF Symbol 占位卡呈现。
// 12 个内容多样的对话，每个都有自己的 transcript（点哪个都有内容可截图）。

enum MockData {

    static func minutesAgo(_ m: Int) -> Date { Date(timeIntervalSinceNow: -Double(m) * 60) }
    static func daysAgo(_ d: Int) -> Date { Date(timeIntervalSinceNow: -Double(d) * 86_400) }

    static let conversations: [ConversationSummary] = [
        .init(id: "c1", title: "30 分钟健康晚餐",
              preview: "三道 30 分钟内能上桌的家常菜，主打高蛋白 + 多蔬菜，附带采购清单……",
              timestamp: minutesAgo(4), pinned: true,
              thumbnailSymbol: "fork.knife", thumbnailTint: .warm),
        .init(id: "c2", title: "全市最大的城市公园",
              preview: "这座城市公园占地超过一千英亩，是半球范围内最大的城市绿地之一。",
              timestamp: minutesAgo(12),
              thumbnailSymbol: "tree.fill", thumbnailTint: .cool),
        .init(id: "c3", title: "小红书广告文案",
              preview: "推广 NLP 课程的小红书文案，标题党 + emoji，五步成稿可复用。",
              timestamp: minutesAgo(26), pinned: true,
              thumbnailSymbol: "megaphone.fill", thumbnailTint: .accent),
        .init(id: "c4", title: "京都三日行程",
              preview: "清水寺、岚山竹林、伏见稻荷——三天紧凑路线，含交通与最佳拍照时段。",
              timestamp: minutesAgo(48),
              thumbnailSymbol: "map.fill", thumbnailTint: .cool),
        .init(id: "c5", title: "Transformer 是怎么工作的",
              preview: "从自注意力到多头机制，配一张结构示意图和最小可运行代码。",
              timestamp: minutesAgo(72),
              thumbnailSymbol: "cpu.fill", thumbnailTint: .accent),
        .init(id: "c6", title: "分布式服务架构示意",
              preview: "网关、服务发现、消息队列、数据层分层展开，给一张架构图。",
              timestamp: minutesAgo(95),
              thumbnailSymbol: "square.grid.3x3.fill", thumbnailTint: .cool),
        .init(id: "c7", title: "Python 销售数据可视化",
              preview: "用 pandas + matplotlib 把季度销售画成柱状图，附完整脚本。",
              timestamp: daysAgo(1),
              thumbnailSymbol: "chart.bar.fill", thumbnailTint: .accent),
        .init(id: "c8", title: "极简日常护肤",
              preview: "清洁、保湿、防晒三步覆盖核心，区分早晚，附敏感肌注意事项。",
              timestamp: daysAgo(1),
              thumbnailSymbol: "drop.fill", thumbnailTint: .cool),
        .init(id: "c9", title: "鸡油菌识别指南",
              preview: "金黄漏斗形、果香、假鳃——附三张特征图与近似毒菌的区别。",
              timestamp: daysAgo(1),
              thumbnailSymbol: "leaf.fill", thumbnailTint: .warm),
        .init(id: "c10", title: "电影的诞生",
              preview: "从十九世纪末的连续摄影实验，到第一批公开放映的活动影像。",
              timestamp: daysAgo(2),
              thumbnailSymbol: "film.fill", thumbnailTint: .mono),
        .init(id: "c11", title: "最稀有的颜料",
              preview: "历史上有些颜料贵到只用于最重要的画作——群青、骨螺紫、雌黄。",
              timestamp: daysAgo(2),
              thumbnailSymbol: "paintpalette.fill", thumbnailTint: .accent),
        .init(id: "c12", title: "SQL 慢查询优化",
              preview: "一条全表扫描的订单统计，加复合索引 + 改写后从 2.3s 降到 40ms。",
              timestamp: daysAgo(3),
              thumbnailSymbol: "cylinder.split.1x2.fill", thumbnailTint: .mono),
    ]

    // MARK: - 每会话 transcript

    /// 点开某会话时的 transcript（mock 模式）。未命中时回落到一段通用占位。
    static func transcript(for id: String) -> [TranscriptItem] {
        transcripts[id] ?? [
            .user(.init(id: "\(id)-u", text: "帮我看看这个。")),
            .assistant(.init(id: "\(id)-a",
                blocks: [.paragraph("这是一段占位回复，用于走查空会话的渲染。")],
                usage: .init(durationS: 0.6, tokens: 120, model: "qwen-plus"))),
        ]
    }

    /// 评分队列（mock 模式按会话给）。
    static func ratings(for id: String) -> [RatingItem] {
        id == "c1" ? [.init(id: 142, skillName: "meal-planner", state: .idle)] : []
    }

    private static let transcripts: [String: [TranscriptItem]] = [
        "c1": [
            .user(.init(id: "c1u1", text: "今晚想吃得健康点，半小时能搞定的，给我几道？")),
            .assistant(.init(id: "c1a1", blocks: [
                .paragraph("没问题，这三道都能在 30 分钟内上桌，主打高蛋白 + 多蔬菜："),
                .heading("一、香煎柠檬鸡胸配芦笋"),
                .image(.init(id: "c1im1", symbol: "fork.knife", caption: "成品示意：金黄鸡胸 + 翠绿芦笋", tint: .warm)),
                .bullets([
                    "鸡胸两块，盐、黑胡椒、柠檬汁腌 10 分钟",
                    "中火煎 4 分钟翻面，再煎 3 分钟",
                    "芦笋同锅快炒 2 分钟，淋少许橄榄油",
                ]),
                .heading("二、番茄鹰嘴豆汤"),
                .paragraph("罐装鹰嘴豆 + 番茄丁 + 洋葱，小火煮 15 分钟，撒孜然和欧芹即可。"),
                .heading("三、牛油果鸡蛋全麦吐司"),
                .paragraph("全麦面包烤脆，铺牛油果泥和水煮蛋，挤一点是拉差。"),
            ],
            toolActivities: [.init(id: "c1t1", name: "recipe_search", state: .done, durationS: 0.7, summary: "3 道")],
            reasoning: "优先选不需要长时间炖煮、食材容易买到的菜谱，控制总时长在 30 分钟内。",
            usage: .init(durationS: 2.4, tokens: 980, model: "qwen-plus"),
            invocationId: 142, skillName: "meal-planner")),
            .user(.init(id: "c1u2", text: "顺便给我一份采购清单")),
            .assistant(.init(id: "c1a2", blocks: [
                .bullets([
                    "鸡胸肉 2 块 · 芦笋 1 把 · 柠檬 1 个",
                    "罐装鹰嘴豆 1 罐 · 番茄丁 1 罐 · 洋葱 1 个",
                    "全麦面包 · 牛油果 2 个 · 鸡蛋 4 枚",
                    "调料：橄榄油、孜然、黑胡椒、是拉差",
                ]),
            ],
            usage: .init(durationS: 1.1, tokens: 240, model: "qwen-plus"))),
        ],

        "c2": [
            .user(.init(id: "c2u1", text: "这座城市最大的公园是哪个？")),
            .assistant(.init(id: "c2a1", blocks: [
                .paragraph("这里最大的公园占地超过一千英亩，是该地区最大的城市公园之一。"),
                .image(.init(id: "c2im1", symbol: "tree.fill", caption: "公园俯瞰示意", tint: .cool)),
                .heading("一片绵延的城市森林"),
                .paragraph("它常被称为城市的「绿肺」，以一座小山为中心，分成若干区域，包含许多景点："),
                .bullets(["一座历史城堡", "一座人类学博物馆", "一座城市动物园", "数个湖泊与文化中心"]),
                .paragraph("它被认为是世界上访问量最大的城市公园之一。"),
            ],
            toolActivities: [.init(id: "c2t1", name: "web_search", state: .done, durationS: 0.8, summary: "3 来源")],
            reasoning: "比对了几个来源的占地数字，选了最一致的描述。",
            sources: ["百科", "+2"],
            usage: .init(durationS: 2.1, tokens: 1200, model: "qwen-plus"),
            invocationId: 143, skillName: "research-summary")),
        ],

        "c3": [
            .user(.init(id: "c3u1", text: "帮我写一条发小红书的广告文案，推广我们的 NLP 课程")),
            .assistant(.init(id: "c3a1", blocks: [
                .heading("标题"),
                .paragraph("别再卷传统开发了，NLP 才是 AI 时代的薪资天花板 🔥"),
                .heading("正文"),
                .paragraph("大模型时代，会调 API 的人满大街都是，但真正懂 NLP 底层的人，才是企业抢着要的稀缺人才。"),
                .bullets([
                    "Transformer 原理 → 不只会调包",
                    "RAG / Agent 实战 → 大厂热门技术栈",
                    "企业级项目带做 → 简历直接有东西写",
                ]),
                .paragraph("适合：想转行 AI 的程序员 / 在校生 / 想涨薪的打工人。早鸟价名额有限。"),
                .paragraph("#西湖大学  #NLP  #AI转行  #大模型  #职场提升"),
            ],
            reasoning: "采用「痛点 → 卖点三连 → 适合人群 → 行动号召」的小红书常见结构，标题用反差 + 数字。",
            usage: .init(durationS: 1.8, tokens: 620, model: "qwen-plus"),
            invocationId: 144, skillName: "xiaohongshu-copywriting", rating: 1)),
            .user(.init(id: "c3u2", text: "把标题改得更标题党一点")),
            .assistant(.init(id: "c3a2", blocks: [
                .paragraph("后悔没早学，NLP 让我从月薪 8k 到年薪 30w 😭"),
                .paragraph("数字冲击 + 情绪反差 + 后悔体——小红书的流量密码。"),
            ],
            usage: .init(durationS: 0.9, tokens: 180, model: "qwen-plus"),
            invocationId: 145, skillName: "xiaohongshu-copywriting")),
        ],

        "c4": [
            .user(.init(id: "c4u1", text: "帮我排一个京都三日游，喜欢古迹和自然")),
            .assistant(.init(id: "c4a1", blocks: [
                .heading("Day 1 · 东山古迹线"),
                .image(.init(id: "c4im1", symbol: "building.columns.fill", caption: "清水寺示意", tint: .warm)),
                .bullets(["清水寺（建议 8:00 前到，避开人潮）", "二年坂 / 三年坂石板街", "八坂神社 + 祇园夜景"]),
                .heading("Day 2 · 岚山自然线"),
                .image(.init(id: "c4im2", symbol: "mountain.2.fill", caption: "岚山竹林示意", tint: .cool)),
                .bullets(["竹林小径（清晨光线最好）", "渡月桥", "天龙寺庭园"]),
                .heading("Day 3 · 伏见 + 宇治"),
                .bullets(["伏见稻荷千本鸟居", "宇治平等院 + 抹茶"]),
            ],
            toolActivities: [.init(id: "c4t1", name: "maps_route", state: .done, durationS: 1.2, summary: "已优化")],
            usage: .init(durationS: 3.0, tokens: 1450, model: "qwen-plus"),
            invocationId: 146, skillName: "trip-planner")),
        ],

        "c5": [
            .user(.init(id: "c5u1", text: "用通俗的话讲讲 Transformer 是怎么工作的，最好有图和代码")),
            .assistant(.init(id: "c5a1", blocks: [
                .paragraph("核心就一句话：让每个词「看一眼」句子里所有其他词，再决定该关注谁——这就是自注意力。"),
                .image(.init(id: "c5im1", symbol: "cpu.fill", caption: "自注意力 + 多头结构示意", tint: .accent)),
                .heading("最小可运行示例"),
                .code(language: "python", text: """
import torch, torch.nn.functional as F

def attention(Q, K, V):
    scores = Q @ K.transpose(-2, -1) / (Q.size(-1) ** 0.5)
    weights = F.softmax(scores, dim=-1)
    return weights @ V

# 多头：把维度切成 h 份分别做 attention 再拼回来
"""),
                .bullets([
                    "Q/K/V：每个词的三种投影",
                    "缩放点积：算相似度并归一化",
                    "多头：并行关注不同的语义子空间",
                ]),
            ],
            reasoning: "先给一句直觉，再给最小代码，最后用要点收束，避免一上来堆公式。",
            sources: ["论文摘要", "+1"],
            usage: .init(durationS: 2.7, tokens: 1320, model: "qwen-plus"),
            invocationId: 147, skillName: "concept-explainer")),
        ],

        "c6": [
            .user(.init(id: "c6u1", text: "画个分布式服务的架构示意：网关、服务发现、消息队列、数据层")),
            .assistant(.init(id: "c6a1", blocks: [
                .paragraph("给你一张分层示意，请求自上而下流过四层："),
                .image(.init(id: "c6im1", symbol: "square.grid.3x3.fill", caption: "微服务分层架构图", tint: .cool)),
                .bullets([
                    "接入层：API 网关（鉴权、限流、路由）",
                    "服务层：各微服务 + 服务发现（注册/心跳）",
                    "异步层：消息队列解耦削峰",
                    "数据层：主从库 + 缓存 + 对象存储",
                ]),
            ],
            toolActivities: [.init(id: "c6t1", name: "diagram_render", state: .done, durationS: 1.5, summary: "1 张")],
            usage: .init(durationS: 2.2, tokens: 760, model: "qwen-plus"),
            invocationId: 148, skillName: "architecture-diagram", rating: 1)),
        ],

        "c7": [
            .user(.init(id: "c7u1", text: "用 Python 把这份季度销售画成柱状图")),
            .assistant(.init(id: "c7a1", blocks: [
                .paragraph("用 pandas 读数据、matplotlib 出图，完整脚本如下："),
                .code(language: "python", text: """
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("sales.csv")
q = df.groupby("quarter")["revenue"].sum()

q.plot(kind="bar", color="#1aa89e")
plt.title("Quarterly Revenue")
plt.ylabel("¥ (万)")
plt.tight_layout()
plt.savefig("revenue.png", dpi=160)
"""),
                .image(.init(id: "c7im1", symbol: "chart.bar.fill", caption: "revenue.png 输出预览", tint: .accent)),
                .paragraph("想要折线或堆叠柱状的话，把 kind 换成 line / 传 stacked=True 即可。"),
            ],
            toolActivities: [
                .init(id: "c7t1", name: "python_exec", state: .done, durationS: 1.9, summary: "出图成功"),
            ],
            usage: .init(durationS: 3.1, tokens: 880, model: "qwen-plus"),
            invocationId: 149, skillName: "data-viz")),
        ],

        "c8": [
            .user(.init(id: "c8u1", text: "给我一套极简日常护肤，我是混合偏油")),
            .assistant(.init(id: "c8a1", blocks: [
                .heading("早晨"),
                .bullets(["温和氨基酸洁面", "清爽保湿乳", "防晒 SPF30+（最关键的一步）"]),
                .heading("夜晚"),
                .bullets(["洁面", "保湿（偏油可选啫喱质地）", "每周 2-3 次低浓度水杨酸"]),
                .paragraph("混合偏油重点是「控油但不过度清洁」，别天天去角质。"),
            ],
            usage: .init(durationS: 1.4, tokens: 420, model: "qwen-plus"),
            invocationId: 150, skillName: "skincare-advisor")),
        ],

        "c9": [
            .user(.init(id: "c9u1", text: "怎么识别鸡油菌？别采到毒的")),
            .assistant(.init(id: "c9a1", blocks: [
                .paragraph("鸡油菌有三个硬特征，缺一不可："),
                .image(.init(id: "c9im1", symbol: "leaf.fill", caption: "鸡油菌特征：漏斗形 + 假鳃", tint: .warm)),
                .bullets([
                    "整体金黄，倒漏斗 / 喇叭形",
                    "明显的杏子 / 果香",
                    "菌褶是「假鳃」——浅脊状、向下延伸到菌柄，不易剥离",
                ]),
                .paragraph("最危险的近似种是「假鸡油菌」和发光类菇——它们的菌褶是真正分明的薄片，且没有果香。拿不准就别吃。"),
            ],
            reasoning: "强调可证伪的区分特征（假鳃 vs 真鳃、气味），并给出最常见的混淆种。",
            sources: ["真菌图鉴", "+2"],
            usage: .init(durationS: 2.0, tokens: 690, model: "qwen-plus"),
            invocationId: 151, skillName: "field-guide")),
        ],

        "c10": [
            .user(.init(id: "c10u1", text: "电影是怎么诞生的？简单讲讲")),
            .assistant(.init(id: "c10a1", blocks: [
                .image(.init(id: "c10im1", symbol: "film.fill", caption: "早期活动影像装置示意", tint: .mono)),
                .paragraph("十九世纪末，人们用快速连续拍摄的照片制造「动起来」的错觉。关键几步："),
                .bullets([
                    "连续摄影实验：用一排相机拍奔马，证明四蹄会同时离地",
                    "赛璐珞胶片：让长条连续影像成为可能",
                    "放映机：第一批向公众售票放映的活动影像出现",
                ]),
                .paragraph("从此「电影」从实验室走向大众娱乐。"),
            ],
            usage: .init(durationS: 1.7, tokens: 560, model: "qwen-plus"))),
        ],

        "c11": [
            .user(.init(id: "c11u1", text: "历史上最稀有 / 最贵的颜料有哪些？")),
            .assistant(.init(id: "c11a1", blocks: [
                .image(.init(id: "c11im1", symbol: "paintpalette.fill", caption: "群青 · 骨螺紫 · 雌黄 色样", tint: .accent)),
                .bullets([
                    "群青（Ultramarine）：由青金石研磨，曾比黄金还贵，多用于圣母衣袍",
                    "骨螺紫（Tyrian Purple）：从海螺提取，数千只螺才得几克，象征皇权",
                    "雌黄（Orpiment）：明亮的柠檬黄，含砷，有毒",
                ]),
                .paragraph("正因稀有昂贵，这些颜色在历史上常被「留给」最重要的人物或作品。"),
            ],
            sources: ["艺术史", "+1"],
            usage: .init(durationS: 1.9, tokens: 640, model: "qwen-plus"),
            invocationId: 152, skillName: "research-summary", rating: 1)),
        ],

        "c12": [
            .user(.init(id: "c12u1", text: "这条订单统计 SQL 要 2 秒多，怎么优化？")),
            .assistant(.init(id: "c12a1", blocks: [
                .paragraph("问题在于按 user_id + created_at 过滤却没有复合索引，导致全表扫描。两步走："),
                .heading("1. 加复合索引"),
                .code(language: "sql", text: "CREATE INDEX idx_orders_user_time\n  ON orders (user_id, created_at);"),
                .heading("2. 改写：避免对索引列做函数"),
                .code(language: "sql", text: """
-- 慢：DATE(created_at) 会让索引失效
-- 快：用范围条件
SELECT user_id, COUNT(*), SUM(amount)
FROM orders
WHERE user_id = ?
  AND created_at >= ? AND created_at < ?
GROUP BY user_id;
"""),
                .paragraph("实测：2.3s → 40ms。"),
            ],
            toolActivities: [
                .init(id: "c12t1", name: "explain_analyze", state: .done, durationS: 0.5, summary: "index scan"),
                .init(id: "c12t2", name: "sql_exec", state: .done, durationS: 0.04, summary: "40ms"),
            ],
            reasoning: "先定位全表扫描根因，再给「索引 + 不破坏索引的改写」两步，最后用前后耗时佐证。",
            usage: .init(durationS: 2.5, tokens: 910, model: "qwen-plus"),
            invocationId: 153, skillName: "sql-optimizer", rating: 1)),
        ],
    ]

    // MARK: - 兼容旧引用（#Preview 等）

    static var sampleTranscript: [TranscriptItem] { transcript(for: "c2") }
    static var sampleRatings: [RatingItem] { [.init(id: 143, skillName: "research-summary", state: .idle)] }

    static var sampleScope: ScopeQuestion {
        .init(id: "rename-batch", skillName: "Batch Rename")
    }

    static var sampleClarify: ClarifyPrompt {
        .init(id: "req42",
              question: "你这套「批量重命名 + 提交」的做法，要不要存成一个可复用技能？",
              choices: ["好，存为技能", "不用了", "这次不要"])
    }
}
