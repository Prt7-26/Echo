import SwiftUI
import AppKit

/// AppKit 视觉效果背景（vibrancy）。`.behindWindow` 混合模式让桌面/壁纸透过来——
/// 这是 Finder/Mail/Siri 半透 sidebar 的标准做法，比 SwiftUI Material 更透、更贴系统。
struct VisualEffectBackground: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .sidebar
    var blending: NSVisualEffectView.BlendingMode = .behindWindow

    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = material
        v.blendingMode = blending
        v.state = .followsWindowActiveState
        return v
    }
    func updateNSView(_ v: NSVisualEffectView, context: Context) {
        v.material = material
        v.blendingMode = blending
    }
}

/// 把承载窗口设为「非不透明 + 透明背景」。这是 `.behindWindow` vibrancy 能真正
/// 透出桌面/壁纸的前提——否则毛玻璃只能贴着白色窗口背板模糊，看起来发白（用户反馈「白白的」）。
/// 右侧对话区各面板都铺了 `Theme.contentBackground` 实底，所以只有 sidebar 会透，内容区不受影响。
struct WindowVibrancyConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        apply(from: v)
        return v
    }
    func updateNSView(_ nsView: NSView, context: Context) { apply(from: nsView) }

    private func apply(from view: NSView) {
        DispatchQueue.main.async { [weak view] in
            guard let window = view?.window else { return }
            window.isOpaque = false
            window.backgroundColor = .clear
        }
    }
}
