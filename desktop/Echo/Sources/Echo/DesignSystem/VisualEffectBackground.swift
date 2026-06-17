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

/// 规整承载窗口。默认走「稳定路线」：保持窗口不透明 + 正常受管理 + 有阴影——
/// 彻底避免非不透明窗口在本机引发的合成卡顿（按钮卡、调度中心激活卡、阴影重算卡）。
///
/// `ECHO_GLASS_WINDOW=1` 才开「透壁纸路线」：把窗口设非不透明 + 透明背景 + 关阴影，
/// 让侧栏 `.behindWindow` vibrancy 真正透出桌面——代价是窗口服务器每次重合成它都可能卡
/// （从调度中心激活等），仅推荐不在意此点、追求极致透壁纸时开启。
struct WindowVibrancyConfigurator: NSViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }
    final class Coordinator { var configured = false }

    private var glassWindow: Bool {
        ProcessInfo.processInfo.environment["ECHO_GLASS_WINDOW"] == "1"
    }

    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        configureOnce(v, context.coordinator)
        return v
    }

    /// 只在「还没配过」时配一次（updateNSView 频繁调用，绝不每次改窗口属性）。
    func updateNSView(_ nsView: NSView, context: Context) {
        configureOnce(nsView, context.coordinator)
    }

    private func configureOnce(_ view: NSView, _ coord: Coordinator) {
        guard !coord.configured else { return }
        let glass = glassWindow
        DispatchQueue.main.async { [weak view] in
            guard let window = view?.window else { return }  // 窗口还没 attach → 等下次 update 再试
            // 两条路线都规整为正常受管理窗口（参与 Spaces/调度中心、正常层级）。
            window.level = .normal
            window.collectionBehavior = [.managed, .participatesInCycle, .fullScreenPrimary]
            if glass {
                // 透壁纸路线：非不透明 + 透明背景 + 关阴影（透明窗口阴影重算会卡）。
                window.isOpaque = false
                window.backgroundColor = .clear
                window.hasShadow = false
            } else {
                // 稳定路线：保持不透明 + 有阴影（默认即如此，显式声明以防被改）。
                window.isOpaque = true
                window.hasShadow = true
            }
            coord.configured = true
            uiLog("window configured (glass=\(glass)): opaque=\(window.isOpaque) shadow=\(window.hasShadow) behavior=\(window.collectionBehavior.rawValue)")
        }
    }
}
