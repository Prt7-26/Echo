import SwiftUI

/// 集中式设计令牌。一处调参，全局引用。
/// 数值来自 DevPlan/siri-app-ui-plan.md §5 与 siri-app-wireframes.md「尺寸速查」。
enum Tokens {

    // MARK: 圆角
    enum Radius {
        static let window: CGFloat = 18
        static let card: CGFloat = 14
        static let button: CGFloat = 10
        static let image: CGFloat = 12
        static let chip: CGFloat = 8
    }

    // MARK: 间距
    enum Spacing {
        static let cardGutter: CGFloat = 12
        static let cardPadding: CGFloat = 10
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

    // MARK: 时序
    enum Timing {
        /// 流式 message.delta 合批刷新间隔
        static let streamFlush: Duration = .milliseconds(16)
        /// 评分 widget 撤销 / 补充理由窗口
        static let ratingUndoWindow: Duration = .seconds(60)
        /// Echo 信号轮询节奏（对齐 dashboard bundle）
        static let signalPoll: Duration = .seconds(5)
    }

    // MARK: 字体
    enum Typeface {
        static let body = Font.body
        static let callout = Font.callout
        static let cardTitle = Font.system(.subheadline, weight: .semibold)
        static let cardTimestamp = Font.system(.caption2)
        /// 助手回复里的大标题 —— New York 衬线
        static let serifTitle = Font.system(.title, design: .serif)
        static let serifTitle2 = Font.system(.title2, design: .serif)
        static let mono = Font.system(.callout, design: .monospaced)
    }
}
