import SwiftUI
import AppKit

/// 直接操作 NavigationSplitView 自带的系统 `NSVisualEffectView`（唯一能透出桌面的层）：
/// 把它锁成常驻 `.active`，并在窗口失焦/激活时**重新锁回**——否则系统会在失焦时把它改回
/// 不透明（用户反馈「失焦不透」）。只改 `state`，不碰 isOpaque/背景/层级。
struct ActiveVibrancy: NSViewRepresentable {
    func makeCoordinator() -> Coordinator { Coordinator() }

    @MainActor final class Coordinator {
        weak var window: NSWindow?
        var installed = false
        var observers: [NSObjectProtocol] = []

        func attach(_ w: NSWindow) {
            window = w
            forceActive()
            guard !installed else { return }
            installed = true
            let nc = NotificationCenter.default
            let names: [Notification.Name] = [
                NSWindow.didResignKeyNotification, NSWindow.didBecomeKeyNotification,
                NSWindow.didResignMainNotification, NSWindow.didBecomeMainNotification,
            ]
            for name in names {
                observers.append(nc.addObserver(forName: name, object: w, queue: .main) { [weak self] _ in
                    MainActor.assumeIsolated {
                        // 下一拍再锁：等系统先把它改成 inactive，我们再覆盖回 .active。
                        DispatchQueue.main.async { self?.forceActive() }
                    }
                })
            }
        }

        func forceActive() {
            guard let root = window?.contentView else { return }
            let dump = ProcessInfo.processInfo.environment["ECHO_DUMP_VIEWS"] == "1"
            var count = 0
            Coordinator.walk(root, dump: dump, count: &count)
            if dump {
                FileHandle.standardError.write(Data("[echo-ui] forceActive: \(count) NSVisualEffectView found\n".utf8))
            }
        }

        static func walk(_ v: NSView, dump: Bool, count: inout Int) {
            for s in v.subviews {
                if let fx = s as? NSVisualEffectView {
                    fx.state = .active; count += 1
                    if dump {
                        FileHandle.standardError.write(Data(
                            "[echo-ui] sysFX material=\(fx.material.rawValue) blend=\(fx.blendingMode.rawValue) frame=\(fx.frame)\n".utf8))
                    }
                }
                walk(s, dump: dump, count: &count)
            }
        }
    }

    func makeNSView(context: Context) -> NSView {
        let v = NSView()
        let coord = context.coordinator
        DispatchQueue.main.async { [weak v] in if let w = v?.window { coord.attach(w) } }
        return v
    }
    func updateNSView(_ nsView: NSView, context: Context) {
        let coord = context.coordinator
        DispatchQueue.main.async { [weak nsView] in if let w = nsView?.window { coord.attach(w) } }
    }
}
