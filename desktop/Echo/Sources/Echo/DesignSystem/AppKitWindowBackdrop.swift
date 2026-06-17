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

        // ① 去掉窗口自己的黑色底 + 让背板/内容延伸到标题栏区（去掉顶部黑条）。
        //    不动 isOpaque（保持默认，不卡）；只清背景色，让玻璃背板成为最底层、直贴桌面。
        window.backgroundColor = .clear
        window.titlebarAppearsTransparent = true
        window.styleMask.insert(.fullSizeContentView)
        window.isMovableByWindowBackground = true

        // ② 背板玻璃只创建一次；之后只更新尺寸。
        //    尺寸用 frameView.bounds（铺满整个窗口，含标题栏区与四周边缘），而非
        //    contentView.frame（它没铺满 → 缝里露出窗口黑色 → 用户看到的黑边框）。
        if let existing = frameView.subviews.first(where: { $0.identifier == backdropID }) {
            existing.frame = frameView.bounds
        } else if ProcessInfo.processInfo.environment["ECHO_BACKDROP_DEBUG"] == "1" {
            // 诊断：背板换成亮品红实心。变品红处=背板在透；仍黑处=上面压着覆盖层。
            let probe = NSView()
            probe.identifier = backdropID
            probe.wantsLayer = true
            probe.layer?.backgroundColor = NSColor.magenta.cgColor
            probe.frame = frameView.bounds
            probe.autoresizingMask = [.width, .height]
            frameView.addSubview(probe, positioned: .below, relativeTo: contentView)
        } else {
            let fx = NSVisualEffectView()
            fx.identifier = backdropID
            fx.material = material
            fx.blendingMode = .behindWindow      // 透出窗口背后的桌面/壁纸
            fx.state = .active                   // 常驻 active：失焦也不变暗
            fx.frame = frameView.bounds
            fx.autoresizingMask = [.width, .height]
            frameView.addSubview(fx, positioned: .below, relativeTo: contentView)
        }

        // ③ 每次都重新把承载视图设透明（失焦/重绘可能被系统重置回不透明 → 发黑）。
        contentView.wantsLayer = true
        contentView.layer?.isOpaque = false
        contentView.layer?.backgroundColor = NSColor.clear.cgColor

        // 临时诊断：递归 dump frameView 子树（找出顶部/底部黑边是哪个视图）。
        if ProcessInfo.processInfo.environment["ECHO_DUMP_VIEWS"] == "1" {
            uiLog("=== frameView \(frameView.frame) tree (back→front) ===")
            dumpTree(frameView, depth: 0)
        }

        // ④ NavigationSplitView 给 sidebar 列自带一层系统 vibrancy，默认 followsWindowActiveState
        //    → 窗口失焦时它变不透明、盖住背板（用户反馈「失焦就不透」）。把窗口里所有系统
        //    vibrancy（除我们的背板）设成常驻 .active：失焦不变暗，且与背板叠加更轻。
        forceActiveVibrancy(contentView)
    }

    /// 临时诊断：递归打印视图树（class/frame/opaque/layerBG），定位黑边视图。
    private static func dumpTree(_ view: NSView, depth: Int) {
        let pad = String(repeating: "  ", count: depth)
        for sub in view.subviews {
            let bg = (sub.layer?.backgroundColor).map { c -> String in
                let comps = c.components ?? []
                return "rgba(\(comps.map { String(format: "%.2f", $0) }.joined(separator: ",")))"
            } ?? "nil"
            let fxInfo = (sub as? NSVisualEffectView).map { " material=\($0.material.rawValue) state=\($0.state.rawValue)" } ?? ""
            uiLog("\(pad)\(type(of: sub)) f=\(sub.frame) op=\(sub.isOpaque) bg=\(bg)\(fxInfo)")
            if depth < 2 { dumpTree(sub, depth: depth + 1) }
        }
    }

    /// 递归把所有系统 NSVisualEffectView（除背板）设为常驻 active，消除失焦变暗。
    private static func forceActiveVibrancy(_ view: NSView) {
        for sub in view.subviews {
            if let fx = sub as? NSVisualEffectView, fx.identifier != backdropID {
                fx.state = .active
            }
            forceActiveVibrancy(sub)
        }
    }
}
