import SwiftUI
import AppKit

/// AppKit `NSSplitViewController` 分栏（替代 SwiftUI NavigationSplitView）。
///
/// 为什么用 AppKit：NavigationSplitView 的侧栏半透是 SwiftUI 窗口层内部实现，拿不到、
/// 控不了（改不了失焦状态、去不掉边框）。`NSSplitViewItem(sidebarWithViewController:)`
/// 给的是**真正的系统 sidebar**——一个我能遍历到的 `NSVisualEffectView`：能锁 `.active`
/// （失焦也透）、能满铺去边框、`.behindWindow` 透出桌面（Finder/WeChat 同一机制）。
/// SwiftUI 内容用 `NSHostingController` 塞进每一栏。
struct AppKitSplitView: NSViewControllerRepresentable {
    let app: AppState

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSViewController(context: Context) -> NSSplitViewController {
        let split = NSSplitViewController()
        split.view.wantsLayer = true

        // 侧栏：系统 sidebar 行为（自带可控的 behindWindow 半透材质）。
        let sidebarVC = NSHostingController(rootView: ConversationGallery(app: app))
        sidebarVC.view.wantsLayer = true
        sidebarVC.view.layer?.backgroundColor = NSColor.clear.cgColor   // 透明，露出 sidebar 材质
        let sidebarItem = NSSplitViewItem(sidebarWithViewController: sidebarVC)
        sidebarItem.minimumThickness = Tokens.Size.sidebarMin
        sidebarItem.maximumThickness = Tokens.Size.sidebarMax
        sidebarItem.canCollapse = true
        sidebarItem.allowsFullHeightLayout = true   // 侧栏延伸到标题栏下，半透到顶
        sidebarItem.titlebarSeparatorStyle = .none  // 去掉栏间分隔线（那条边框）
        split.addSplitViewItem(sidebarItem)

        // detail：实底内容层。
        let detailVC = NSHostingController(rootView: DetailContainer(app: app))
        let detailItem = NSSplitViewItem(viewController: detailVC)
        detailItem.minimumThickness = Tokens.Size.detailMin
        detailItem.titlebarSeparatorStyle = .none
        split.addSplitViewItem(detailItem)

        context.coordinator.attach(split)
        return split
    }

    func updateNSViewController(_ controller: NSSplitViewController, context: Context) {
        context.coordinator.forceActive()
    }

    @MainActor final class Coordinator {
        weak var split: NSSplitViewController?
        var observers: [NSObjectProtocol] = []
        var installed = false

        func attach(_ split: NSSplitViewController) {
            self.split = split
            DispatchQueue.main.async { [weak self] in self?.setup() }
        }

        func setup() {
            forceActive()
            guard !installed, let window = split?.view.window else {
                // 窗口还没 attach → 下一拍重试。
                DispatchQueue.main.async { [weak self] in
                    guard let self, !self.installed else { return }
                    self.setup()
                }
                return
            }
            installed = true
            let nc = NotificationCenter.default
            let names: [Notification.Name] = [
                NSWindow.didBecomeKeyNotification, NSWindow.didResignKeyNotification,
                NSWindow.didBecomeMainNotification, NSWindow.didResignMainNotification,
            ]
            for name in names {
                observers.append(nc.addObserver(forName: name, object: window, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated {
                        DispatchQueue.main.async { self?.forceActive() }
                    }
                })
            }
        }

        /// 把侧栏的系统 NSVisualEffectView 锁成常驻 .active（失焦也透）。
        func forceActive() {
            guard let root = split?.view else { return }
            Coordinator.walk(root)
        }

        static func walk(_ v: NSView) {
            for s in v.subviews {
                (s as? NSVisualEffectView)?.state = .active
                walk(s)
            }
        }
    }
}
