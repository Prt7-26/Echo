import SwiftUI

/// 集中式设计令牌。一处调参，全局引用。
/// 数值来自 DevPlan/siri-app-ui-plan.md §5 与 siri-app-wireframes.md「尺寸速查」。
enum Tokens {

    // MARK: 圆角
    enum Radius {
        static let window: CGFloat = 18
        static let card: CGFloat = 14
        static let cardLarge: CGFloat = 20   // 侧栏会话卡（更圆润，对齐 Siri）
        static let button: CGFloat = 10
        static let image: CGFloat = 12
        static let chip: CGFloat = 8
    }

    // MARK: 间距
    enum Spacing {
        static let cardGutter: CGFloat = 16   // 侧栏卡片间距（更大，对齐 Siri）
        static let cardPadding: CGFloat = 12
        static let content: CGFloat = 16
        static let tight: CGFloat = 6
        static let loose: CGFloat = 24
    }

    // MARK: 尺寸
    enum Size {
        static let sidebarMin: CGFloat = 180
        static let sidebarIdeal: CGFloat = 220
        static let sidebarMax: CGFloat = 400
        static let detailMin: CGFloat = 400
        static let windowMinWidth: CGFloat = 760
        static let windowMinHeight: CGFloat = 480
        static let cardThumbHeight: CGFloat = 120
    }

    /// 顶栏整排上抬量（让 44pt 圆按钮的中心与红绿灯齐平）。红绿灯被 AppKit 锁死搬不动，
    /// 故改抬顶栏。可用 ECHO_TOPBAR_DY 实时试，定下后焊死。
    static var topBarRaise: CGFloat {
        if let s = ProcessInfo.processInfo.environment["ECHO_TOPBAR_DY"], let v = Double(s) {
            return CGFloat(v)
        }
        return 5
    }

    // MARK: 时序
    enum Timing {
        /// 流式 message.delta 合批刷新间隔
        static let streamFlush: Duration = .milliseconds(16)
        /// 评分 widget 撤销 / 补充理由窗口
        static let ratingUndoWindow: Duration = .seconds(60)
        /// Echo 信号轮询节奏（对齐 dashboard bundle）
        static let signalPoll: Duration = .seconds(5)
    }

    // MARK: 字体（整体比系统默认偏大一档，更易读；改这里全局生效）
    enum Typeface {
        static let body = Font.system(size: 15)              // 对话正文（默认 ~13 → 15）
        static let callout = Font.system(size: 14)
        static let navTitle = Font.system(size: 17, weight: .semibold)   // 对话区顶栏标题
        static let cardTitle = Font.system(size: 15, weight: .semibold)
        static let cardPreview = Font.system(size: 13)       // 卡片摘要（原 .caption ~11 → 13）
        static let cardTimestamp = Font.system(size: 12)
        static let meta = Font.system(size: 12)              // 工具/用量/次级
        static let metaSmall = Font.system(size: 11)
        /// 助手回复里的大标题 —— New York 衬线
        static let serifTitle = Font.system(size: 24, design: .serif)
        static let serifTitle2 = Font.system(size: 20, design: .serif)
        static let mono = Font.system(size: 13, design: .monospaced)
    }
}
