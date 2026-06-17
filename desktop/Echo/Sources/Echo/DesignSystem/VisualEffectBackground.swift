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
    func makeCoordinator() -> Coordinator { Coordinator() }
    final class Coordinator { var configured = false }

    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        configureOnce(v, context.coordinator)
        return v
    }

    /// 关键：只在「还没配过」时配一次。绝不在每次 update 都改窗口属性——否则点击触发的
    /// 玻璃 morph 动画会每帧改 isOpaque/backgroundColor → 窗口重合成 → 又触发 update，
    /// 在非不透明窗口 + Liquid Glass 下滚成重合成风暴 / 主线程卡死（彩虹球）。
    func updateNSView(_ nsView: NSView, context: Context) {
        configureOnce(nsView, context.coordinator)
    }

    private func configureOnce(_ view: NSView, _ coord: Coordinator) {
        guard !coord.configured else { return }
        DispatchQueue.main.async { [weak view] in
            guard let window = view?.window else { return }  // 窗口还没 attach → 等下次 update 再试
            window.isOpaque = false
            window.backgroundColor = .clear
            // 非不透明窗口默认可能被窗口服务器排除出 Mission Control/Exposé 动画（停在原处不缩放）。
            // 显式规整为「正常受管理窗口」：参与 Spaces/调度中心、正常层级、有阴影。
            window.level = .normal
            window.hasShadow = true
            window.collectionBehavior = [.managed, .participatesInCycle, .fullScreenPrimary]
            coord.configured = true
            FileHandle.standardError.write(Data(
                "[echo-ui] window configured: opaque=\(window.isOpaque) level=\(window.level.rawValue) behavior=\(window.collectionBehavior.rawValue)\n".utf8))
        }
    }
}
