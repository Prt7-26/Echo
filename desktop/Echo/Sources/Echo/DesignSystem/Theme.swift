import SwiftUI
import AppKit

/// 配色与强调色。Echo 信号区沿用 sonar-teal（与 CLI/TUI skin 同源），
/// 其余遵循系统中性暖灰 + 大量留白（截图风格）。
enum Theme {
    /// Echo 主强调色 sonar-teal（对齐 plugins/echo_signals/skin/echo.yaml）。
    static let accent = Color(red: 0.10, green: 0.66, blue: 0.62)

    /// Echo 品牌标记：波形/脉冲线。单一来源，改一处即可全局生效。
    static let logoSymbol = "waveform.path.ecg"

    /// 用户气泡底色（浅灰 pill）。
    static let userBubble = Color.secondary.opacity(0.14)

    /// 次级文字（卡片摘要、meta 行）。
    static let secondaryText = Color.secondary

    // MARK: 表面色（HIG：内容层用实底，不用 Liquid Glass）
    /// 内容卡片实底（会话卡）——随明暗自适应的控件/内容背景。
    static let cardSurface = Color(nsColor: .controlBackgroundColor)
    /// 对话/内容区实底（transcript、侧面板）。
    static let contentBackground = Color(nsColor: .textBackgroundColor)
    /// 内嵌内容面（回复内的工具/推理/代码/来源）——更淡的填充。
    static let insetSurface = Color.secondary.opacity(0.09)
    /// 发丝分隔线。
    static let hairline = Color(nsColor: .separatorColor)

    /// 信号语义色（与 dashboard SIGNAL_BADGES 同语义）。
    enum Signal {
        static let positive = Color.green
        static let negative = Color.red
        static let drift = Color.orange
        static let nomination = Color.yellow       // m1_save_intent / recurrence (amber)
        static let clipboard = Color.cyan
        static let nlPositive = Color.green.opacity(0.7)
        static let nlNegative = Color.red.opacity(0.7)
    }

    /// 置信度状态色。
    static func confidenceColor(_ value: Double) -> Color {
        switch value {
        case ..<0.5: return .red
        case ..<0.75: return .orange
        default: return .green
        }
    }
}
