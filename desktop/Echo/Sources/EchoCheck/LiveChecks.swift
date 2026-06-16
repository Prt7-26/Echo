import Foundation
import EchoKit

// 真后端 live 集成自检。仅当 ECHO_APP_LIVE=1 时运行（默认离线，不依赖后端）。
// 真的把 `python -m tui_gateway.entry` 拉起，验证 spawn→ready→session.list 全链路。
//
// 用法：
//   ECHO_APP_LIVE=1 ECHO_REPO_ROOT=/path/to/Echo HERMES_PYTHON=/path/to/python \
//     swift run echo-check

private func diag(_ s: String) {
    FileHandle.standardError.write(Data(("    [live] " + s + "\n").utf8))
}

/// 确定性 mock gateway 集成自检（真 stdio 管道，不依赖重型真后端）。
/// 自动定位 scripts/mock_gateway.py；可用 ECHO_APP_MOCK_GW 覆盖路径。
func registerMockGatewayCheck(_ r: CheckRunner) {
    r.checkAsync("MOCK-GW: spawn → ready → session.list → create → prompt stream") {
        let env = ProcessInfo.processInfo.environment
        let python = env["HERMES_PYTHON"] ?? "/usr/bin/python3"
        guard let mockPath = locateMockGateway(env: env) else {
            diag("mock_gateway.py not found — skipping (set ECHO_APP_MOCK_GW)")
            return
        }
        let transport = try StdioSubprocessTransport(
            pythonPath: python,
            repoRoot: (mockPath as NSString).deletingLastPathComponent,
            arguments: [mockPath]
        )
        let client = GatewayClient()
        await client.setCallTimeout(8)
        await client.connect(transport)

        // 收集事件流，验证 prompt.submit 的流式回复。
        let collected = EventCollector()
        let pump = Task { for await ev in client.events { await collected.add(ev) } }

        var ready = false
        for _ in 0..<100 { if await client.state == .ready { ready = true; break }
                           try await Task.sleep(nanoseconds: 50_000_000) }
        try r.expectTrue(ready, "mock gateway never ready")

        let sessions = try await client.listSessions()
        try r.expect(sessions.count, 1, "session.list")
        try r.expect(sessions.first?.id, "mock1")

        let created = try await client.createSession()
        try r.expect(created.sessionId, "mock-new")
        try r.expect(created.info?.model, "mock-model")

        _ = try await client.submitPrompt(session: "mock-new", text: "hi")
        // 等流式 message.complete
        var gotComplete = false
        for _ in 0..<100 {
            if await collected.hasComplete { gotComplete = true; break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        try r.expectTrue(gotComplete, "no message.complete from prompt stream")
        try r.expect(await collected.completeText, "Hello, world.")
        try r.expectTrue(await collected.deltaCount >= 1, "no message.delta")

        pump.cancel()
        await client.disconnect()
    }
}

private actor EventCollector {
    var deltaCount = 0
    var hasComplete = false
    var completeText = ""
    func add(_ ev: ParsedEvent) {
        switch ev.event {
        case .messageDelta: deltaCount += 1
        case .messageComplete(let c): hasComplete = true; completeText = c.text
        default: break
        }
    }
}

private func locateMockGateway(env: [String: String]) -> String? {
    let fm = FileManager.default
    if let p = env["ECHO_APP_MOCK_GW"], fm.fileExists(atPath: p) { return p }
    // 从 repo 根定位
    var roots: [String] = []
    if let root = env["ECHO_REPO_ROOT"] { roots.append(root + "/desktop/Echo/scripts/mock_gateway.py") }
    roots.append(fm.currentDirectoryPath + "/scripts/mock_gateway.py")
    roots.append(fm.currentDirectoryPath + "/desktop/Echo/scripts/mock_gateway.py")
    return roots.first(where: fm.fileExists)
}

func registerLiveChecks(_ r: CheckRunner) {
    registerMockGatewayCheck(r)
    guard ProcessInfo.processInfo.environment["ECHO_APP_LIVE"] == "1" else { return }

    r.checkAsync("LIVE: spawn gateway → gateway.ready → session.list → session.create") {
        guard let resolved = BackendLocator.resolve() else {
            throw CheckError("repo not found — set ECHO_REPO_ROOT to the Echo checkout")
        }
        diag("python=\(resolved.python)")
        diag("repo=\(resolved.repoRoot)")

        let transport = try StdioSubprocessTransport(
            pythonPath: resolved.python,
            repoRoot: resolved.repoRoot,
            onStderrLine: { line in
                if line.contains("Traceback") || line.lowercased().contains("error") {
                    diag("gw stderr: " + String(line.prefix(160)))
                }
            }
        )
        diag("spawned gateway, connecting…")
        let client = GatewayClient()
        await client.setCallTimeout(20)
        await client.setRawFrameLogger { frame in
            diag("⟸ frame: " + String(String(decoding: frame, as: UTF8.self).prefix(120)))
        }
        await client.connect(transport)

        var ready = false
        for i in 0..<600 { // up to ~60s for cold import
            if await client.state == .ready { ready = true; break }
            if i % 50 == 0 { diag("waiting for gateway.ready… \(i/10)s") }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        try r.expectTrue(ready, "gateway never emitted gateway.ready")
        diag("gateway.ready received ✓")

        diag("calling session.list…")
        let sessions = try await client.listSessions()
        diag("session.list → \(sessions.count) sessions")
        if let first = sessions.first {
            diag("newest: \"\(first.title.isEmpty ? String(first.preview.prefix(40)) : String(first.title.prefix(40)))\"")
        }

        diag("calling session.create…")
        let created = try await client.createSession()
        diag("session.create → \(created.sessionId), model=\(created.info?.model ?? "?")")
        try r.expectTrue(!created.sessionId.isEmpty, "empty session id")

        await client.disconnect()
        diag("disconnected, done ✓")
    }
}
