import SwiftUI

/// 配色与强调色。Echo 信号区沿用 sonar-teal（与 CLI/TUI skin 同源），
/// 其余遵循系统中性暖灰 + 大量留白（截图风格）。
enum Theme {
    /// Echo 主强调色 sonar-teal（对齐 plugins/echo_signals/skin/echo.yaml）。
    static let accent = Color(red: 0.10, green: 0.66, blue: 0.62)

    /// 用户气泡底色（浅灰 pill）。
    static let userBubble = Color.secondary.opacity(0.14)

    /// 次级文字（卡片摘要、meta 行）。
    static let secondaryText = Color.secondary

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
