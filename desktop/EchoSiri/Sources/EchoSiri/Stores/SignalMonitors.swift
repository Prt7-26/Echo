import AppKit
import EchoSiriKit

/// 原生采集浏览器沙箱拿不到的两类信号（替代 Tauri 壳，DevPlan 决策 §14.4）：
///   • OS 剪贴板变化 → clipboard_copy（仅长度 + 200 字预览，全文绝不外传/持久化）
///   • 窗口焦点 → window_focus / window_blur
/// 信号经 sink 交给 GatewayCoordinator → EchoAPIClient.clipboardSignal。
@MainActor
final class SignalMonitors {
    private let sink: (ClipboardSignalBody) -> Void
    private var pollTask: Task<Void, Never>?
    private var lastChangeCount: Int
    private var focusObservers: [NSObjectProtocol] = []

    init(sink: @escaping (ClipboardSignalBody) -> Void) {
        self.sink = sink
        self.lastChangeCount = NSPasteboard.general.changeCount
    }

    func start() {
        startClipboardPoll()
        startFocusObservers()
    }

    func stop() {
        pollTask?.cancel(); pollTask = nil
        for o in focusObservers { NotificationCenter.default.removeObserver(o) }
        focusObservers.removeAll()
    }

    // MARK: 剪贴板（每 2s 轮询 changeCount）

    private func startClipboardPoll() {
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                self?.checkClipboard()
            }
        }
    }

    private func checkClipboard() {
        let pb = NSPasteboard.general
        guard pb.changeCount != lastChangeCount else { return }
        lastChangeCount = pb.changeCount
        let text = pb.string(forType: .string) ?? ""
        guard !text.isEmpty else { return }
        // 只外传长度 + 截断预览；全文不出本机。
        sink(ClipboardSignalBody(kind: "clipboard_copy",
                                 length: text.count,
                                 preview: String(text.prefix(200))))
    }

    // MARK: 窗口焦点

    private func startFocusObservers() {
        let nc = NotificationCenter.default
        focusObservers.append(nc.addObserver(
            forName: NSApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
                self?.sink(ClipboardSignalBody(kind: "window_focus"))
            })
        focusObservers.append(nc.addObserver(
            forName: NSApplication.willResignActiveNotification, object: nil, queue: .main) { [weak self] _ in
                self?.sink(ClipboardSignalBody(kind: "window_blur"))
            })
    }
}
