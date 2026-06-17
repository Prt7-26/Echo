import SwiftUI
import AppKit

/// 用 AppKit 把一层 `.behindWindow` 的 `NSVisualEffectView` 挂在**窗口最底层**
/// （SwiftUI 承载视图的下面），而不是用 SwiftUI 的 `.background()`。
///
/// 为什么必须这样：SwiftUI 的 `.background(NSVisualEffectView)` 会把玻璃埋进
/// `NSHostingView` 的图层树里，而 hosting view 自身是不透明的 → 玻璃被它垫的实底挡住 →
/// 发黑。Finder/WeChat 的真实做法是把玻璃放到窗口背板层：玻璃在最底透出桌面，SwiftUI
/// 内容浮在上面——sidebar 不铺背景=透明=露出桌面；detail 铺 `Theme.contentBackground`=
/// 实底=遮住玻璃。只有 sidebar 那块透明区与桌面合成，detail 实底区不参与，所以不卡。
struct AppKitWindowBackdrop: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .underWindowBackground

    func makeNSView(context: Context) -> NSView {
        let probe = NSView()
        // 窗口此刻可能还没 attach，下个 runloop 再装；updateNSView 也会兜底重试。
        DispatchQueue.main.async { [weak probe] in
            guard let probe, let window = probe.window else { return }
            Self.install(in: window, material: material)
        }
        return probe
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        guard let window = nsView.window else { return }
        Self.install(in: window, material: material)
    }

    private static let backdropID = NSUserInterfaceItemIdentifier("EchoWindowBackdrop")

    private static func install(in window: NSWindow, material: NSVisualEffectView.Material) {
        guard let contentView = window.contentView,
              let frameView = contentView.superview else { return }
        // 幂等：已装过就只更新尺寸。
        if let existing = frameView.subviews.first(where: { $0.identifier == backdropID }) {
            existing.frame = contentView.frame
            return
        }
        let fx = NSVisualEffectView()
        fx.identifier = backdropID
        fx.material = material
        fx.blendingMode = .behindWindow      // 透出窗口背后的桌面/壁纸
        fx.state = .active                   // 常驻，激活/失活不重渲染
        fx.frame = contentView.frame
        fx.autoresizingMask = [.width, .height]
        // 放在 SwiftUI 承载视图「下面」（窗口背板层）。
        frameView.addSubview(fx, positioned: .below, relativeTo: contentView)

        // 让承载视图透明，背板才能从它的透明区（sidebar 未铺背景处）透上来。
        // detail 区铺了 Theme.contentBackground 实底 → 仍遮住背板，桌面只在 sidebar 露出。
        contentView.wantsLayer = true
        contentView.layer?.isOpaque = false
        contentView.layer?.backgroundColor = NSColor.clear.cgColor
    }
}
