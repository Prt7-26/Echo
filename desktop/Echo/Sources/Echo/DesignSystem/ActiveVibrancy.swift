import SwiftUI
import AppKit

/// 把窗口里所有系统 NSVisualEffectView 设为常驻 `.active`。
///
/// 默认 NavigationSplitView 的 sidebar 材质是 `.followsWindowActiveState`：窗口失焦时
/// 变不透明、不再透出桌面（用户反馈「失焦就不透」）。微信侧栏失焦也透——因为它常驻 active。
/// 这个 helper 只改 `state`（无害、不碰 isOpaque/背景/层级），让侧栏失焦也保持半透。
struct ActiveVibrancy: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        DispatchQueue.main.async { [weak v] in apply(v?.window) }
        return v
    }
    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async { [weak nsView] in apply(nsView?.window) }
    }
    private func apply(_ window: NSWindow?) {
        guard let root = window?.contentView else { return }
        force(root)
    }
    private func force(_ view: NSView) {
        for sub in view.subviews {
            (sub as? NSVisualEffectView)?.state = .active
            force(sub)
        }
    }
}
