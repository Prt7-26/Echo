import SwiftUI
import AppKit

/// AppKit `NSSplitViewController` 分栏。侧栏底层用**我自己创建并持有的**
/// `NSVisualEffectView`（而非系统 `.sidebar` 自动半透——那层在视图树里拿不到、控不了）。
///
/// 我自己的这层是真正的 AppKit `.behindWindow` 视图，放进 AppKit 分栏面板里就会透出桌面
/// （Finder/WeChat 机制；之前埋在 SwiftUI .background 里才不透）。因为是我持有的引用，
/// 可以锁 `.active`（失焦也透）、满铺整块面板（无边框）。SwiftUI 内容浮在它上面。
struct AppKitSplitView: NSViewControllerRepresentable {
    let app: AppState

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSViewController(context: Context) -> NSSplitViewController {
        let split = NSSplitViewController()
        split.view.wantsLayer = true

        // 侧栏：普通 split item，面板底层是我自己的 vibrancy（不用系统 .sidebar 自动层）。
        let sidebarVC = SidebarVibrancyController(app: app)
        let sidebarItem = NSSplitViewItem(viewController: sidebarVC)
        sidebarItem.minimumThickness = Tokens.Size.sidebarMin
        sidebarItem.maximumThickness = Tokens.Size.sidebarMax
        sidebarItem.canCollapse = true
        sidebarItem.allowsFullHeightLayout = true   // 半透延伸到标题栏下
        sidebarItem.titlebarSeparatorStyle = .none  // 去栏间分隔线
        split.addSplitViewItem(sidebarItem)

        // detail：实底内容层。
        let detailVC = NSHostingController(rootView: DetailContainer(app: app))
        let detailItem = NSSplitViewItem(viewController: detailVC)
        detailItem.minimumThickness = Tokens.Size.detailMin
        detailItem.titlebarSeparatorStyle = .none
        split.addSplitViewItem(detailItem)

        context.coordinator.attach(window: { [weak split] in split?.view.window }, effect: sidebarVC.effectView)
        return split
    }

    func updateNSViewController(_ controller: NSSplitViewController, context: Context) {
        context.coordinator.relock()
    }

    @MainActor final class Coordinator {
        private weak var effect: NSVisualEffectView?
        private var windowProvider: (() -> NSWindow?)?
        private var observers: [NSObjectProtocol] = []
        private var installed = false

        func attach(window: @escaping () -> NSWindow?, effect: NSVisualEffectView) {
            self.windowProvider = window
            self.effect = effect
            relock()
            DispatchQueue.main.async { [weak self] in self?.installObservers() }
        }

        private func installObservers() {
            guard !installed, let window = windowProvider?() else {
                DispatchQueue.main.async { [weak self] in
                    guard let self, !self.installed else { return }
                    self.installObservers()
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
                    MainActor.assumeIsolated { DispatchQueue.main.async { self?.relock() } }
                })
            }
        }

        /// 把我的 vibrancy 锁回 .active（失焦也透）。
        func relock() { effect?.state = .active }
    }
}

/// 侧栏面板控制器：view = 我持有的 NSVisualEffectView（满铺、behindWindow、active），
/// SwiftUI ConversationGallery（透明背景）作为子视图浮在其上。
final class SidebarVibrancyController: NSViewController {
    private let app: AppState
    let effectView = NSVisualEffectView()

    init(app: AppState) {
        self.app = app
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) unused") }

    override func loadView() {
        effectView.material = .underWindowBackground
        effectView.blendingMode = .behindWindow      // 透出桌面/壁纸
        effectView.state = .active                   // 失焦也透
        effectView.autoresizingMask = [.width, .height]

        let host = NSHostingView(rootView: ConversationGallery(app: app))
        host.translatesAutoresizingMaskIntoConstraints = false
        effectView.addSubview(host)
        NSLayoutConstraint.activate([
            host.leadingAnchor.constraint(equalTo: effectView.leadingAnchor),
            host.trailingAnchor.constraint(equalTo: effectView.trailingAnchor),
            host.topAnchor.constraint(equalTo: effectView.topAnchor),
            host.bottomAnchor.constraint(equalTo: effectView.bottomAnchor),
        ])
        self.view = effectView
    }
}
