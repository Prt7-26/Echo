import SwiftUI
import AppKit

/// 完全受控的 AppKit 毛玻璃背景（`NSVisualEffectView`）。直接当侧栏底层用——
/// 比依赖 NavigationSplitView 自带的系统材质可控：满铺无边框、`state` 可锁 `.active`
/// （失焦也半透）、`blendingMode` 锁 `.behindWindow`（透出桌面/壁纸）。
struct VisualEffectBackground: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .underWindowBackground
    var state: NSVisualEffectView.State = .active   // 常驻 active：失焦不变暗

    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = material
        v.blendingMode = .behindWindow
        v.state = state
        v.autoresizingMask = [.width, .height]
        return v
    }
    func updateNSView(_ v: NSVisualEffectView, context: Context) {
        v.material = material
        v.blendingMode = .behindWindow
        v.state = state   // 每次更新都锁回 .active，防止被系统改回 followsWindowActiveState
    }
}
