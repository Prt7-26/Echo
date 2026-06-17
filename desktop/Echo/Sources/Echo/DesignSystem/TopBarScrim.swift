import SwiftUI

// Liquid Glass「scroll edge effect」：顶栏不是实心 bar，而是顶部半透磨砂、
// 向下渐隐至完全透明的蒙层——内容从它下面滚过时在上缘柔和淡出（macOS 26
// App Store / 新 Siri 同款）。配合 `.safeAreaInset(edge:.top)` 使用：内容滚到
// 栏下方而非被挤在下面。顶栏属导航层，材质在这里是 HIG 正确用法。

extension View {
    /// 给顶栏内容套上「材质 + 上→下渐隐」蒙层背景，并向上溢出到窗口顶边
    /// （盖住 traffic-light 区一并渐隐）。
    func topBarScrim() -> some View { modifier(TopBarScrimModifier()) }
}

private struct TopBarScrimModifier: ViewModifier {
    // 顶栏按钮直接浮在透明玻璃上（WeChat/Siri 同款），不再铺深色磨砂带——
    // 那条 .ultraThinMaterial 在深色模式下发黑、盖住玻璃背板（用户反馈的顶部黑条）。
    func body(content: Content) -> some View { content }
}
