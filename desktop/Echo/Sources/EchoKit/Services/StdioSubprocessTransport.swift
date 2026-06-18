import Foundation
import os

/// 生产路径的 gateway 传输：把 `python -m tui_gateway.entry`（或 mock）作为子进程
/// 拉起，通过 stdio 收发 newline-delimited JSON-RPC —— 与 ui-tui 的 gatewayClient.ts
/// 完全同构（spawn + readline）。零改 Hermes、零 dashboard 挂载。
///
/// 读路径用 `readabilityHandler` + 手动按 \n 切行（不用 FileHandle.bytes.lines——
/// 后者在子进程管道上会缓冲到读缓冲填满才吐行，实测非确定性丢帧）。
/// 共享状态用 OSAllocatedUnfairLock 守护（async 安全，不跨 await 持锁），
/// 行按到达顺序 FIFO 投递。
public final class StdioSubprocessTransport: GatewayTransport, @unchecked Sendable {

    private struct State {
        var lineBuffer = Data()
        var ready: [Data] = []
        var waiters: [CheckedContinuation<Data, Error>] = []
        var closedError: Error?
    }

    private let process: Process
    private let stdinHandle: FileHandle
    private let stdoutHandle: FileHandle
    // 含非 Sendable 的 CheckedContinuation，故用 uncheckedState。
    private let state = OSAllocatedUnfairLock<State>(uncheckedState: State())

    public init(pythonPath: String,
                repoRoot: String,
                arguments: [String] = ["-m", "tui_gateway.entry"],
                extraEnv: [String: String] = [:],
                onStderrLine: (@Sendable (String) -> Void)? = nil) throws {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonPath)
        proc.arguments = arguments
        proc.currentDirectoryURL = URL(fileURLWithPath: repoRoot)

        var env = ProcessInfo.processInfo.environment
        env["HERMES_PYTHON_SRC_ROOT"] = repoRoot
        env["HERMES_PYTHON"] = pythonPath
        env["PYTHONUNBUFFERED"] = "1"
        for (k, v) in extraEnv { env[k] = v }
        proc.environment = env

        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        proc.standardInput = inPipe
        proc.standardOutput = outPipe
        proc.standardError = errPipe

        self.process = proc
        self.stdinHandle = inPipe.fileHandleForWriting
        self.stdoutHandle = outPipe.fileHandleForReading

        stdoutHandle.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard let self else { return }
            if chunk.isEmpty {
                self.finish(GatewayError.transportClosed)
                handle.readabilityHandler = nil
            } else {
                self.ingest(chunk)
            }
        }

        // stderr 必须始终被抽干：子进程（真 Hermes gateway）启动期会往 stderr 写大量日志，
        // 管道缓冲（~64KB）一满，子进程的下一次 stderr 写就阻塞 → gateway 卡死、永不发
        // gateway.ready → 应用一直停在「连接中」。即使调用方没给 onStderrLine，也要 drain。
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let d = handle.availableData
            guard !d.isEmpty else { handle.readabilityHandler = nil; return }
            if let onStderrLine {
                for line in String(decoding: d, as: UTF8.self).split(separator: "\n") {
                    onStderrLine(String(line))
                }
            } else if Self.traceOn {
                Self.trace("[stderr] " + String(decoding: d, as: UTF8.self).prefix(200))
            }
        }

        try proc.run()
    }

    /// 把一块原始数据并入行缓冲，切出整行，FIFO 满足等待者。
    private func ingest(_ chunk: Data) {
        Self.trace("ingest \(chunk.count)B")
        let toResume: [(CheckedContinuation<Data, Error>, Data)] = state.withLock { s in
            var out: [(CheckedContinuation<Data, Error>, Data)] = []
            s.lineBuffer.append(chunk)
            while let nl = s.lineBuffer.firstIndex(of: 0x0A) {
                let line = s.lineBuffer.subdata(in: s.lineBuffer.startIndex..<nl)
                s.lineBuffer.removeSubrange(s.lineBuffer.startIndex...nl)
                if line.isEmpty { continue }
                if !s.waiters.isEmpty {
                    out.append((s.waiters.removeFirst(), line))
                } else {
                    s.ready.append(line)
                }
            }
            return out
        }
        for (cont, data) in toResume { cont.resume(returning: data) }
    }

    private func finish(_ error: Error) {
        let pending: [CheckedContinuation<Data, Error>] = state.withLock { s in
            if s.closedError == nil { s.closedError = error }
            let w = s.waiters; s.waiters.removeAll()
            return w
        }
        for w in pending { w.resume(throwing: error) }
    }

    // MARK: GatewayTransport

    public func send(_ data: Data) async throws {
        let closed = state.withLock { $0.closedError != nil }
        if closed { throw GatewayError.transportClosed }
        var frame = data
        frame.append(0x0A)
        Self.trace("send \(frame.count)B: \(String(decoding: data, as: UTF8.self).prefix(80))")
        try stdinHandle.write(contentsOf: frame)
        Self.trace("send done")
    }

    public func receive() async throws -> Data {
        enum Action { case resume(Data); case fail(Error); case wait }
        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Data, Error>) in
            let action: Action = state.withLock { s in
                if !s.ready.isEmpty { return .resume(s.ready.removeFirst()) }
                if let e = s.closedError { return .fail(e) }
                s.waiters.append(cont)
                return .wait
            }
            switch action {
            case .resume(let d): cont.resume(returning: d)
            case .fail(let e): cont.resume(throwing: e)
            case .wait: break
            }
        }
    }

    public func close() async {
        stdoutHandle.readabilityHandler = nil
        process.terminate()
        try? stdinHandle.close()
        finish(GatewayError.transportClosed)
    }

    public var isRunning: Bool { process.isRunning }

    private static let traceOn = ProcessInfo.processInfo.environment["ECHO_APP_TRACE"] == "1"
    private static func trace(_ s: String) {
        if traceOn { FileHandle.standardError.write(Data(("    [tx] " + s + "\n").utf8)) }
    }
}
