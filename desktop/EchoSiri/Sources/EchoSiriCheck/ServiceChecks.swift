import Foundation
import EchoSiriKit

// 服务层自检：GatewayClient 请求/响应配对 + 事件流；EchoAPIClient URL/body 构造。
// 无真后端，用 MockGatewayTransport 与注入 fetch 验证逻辑。

private actor RequestRecorder {
    var last: URLRequest?
    func set(_ r: URLRequest) { last = r }
}

private func http200(_ url: URL) -> HTTPURLResponse {
    HTTPURLResponse(url: url, statusCode: 200, httpVersion: nil, headerFields: nil)!
}

/// 从事件流取下一个事件，带超时，避免挂死。
private func nextEvent(_ stream: AsyncStream<ParsedEvent>, timeoutMs: UInt64 = 1000) async -> ParsedEvent? {
    await withTaskGroup(of: ParsedEvent?.self) { group in
        group.addTask {
            for await e in stream { return e }
            return nil
        }
        group.addTask {
            try? await Task.sleep(nanoseconds: timeoutMs * 1_000_000)
            return nil
        }
        let r = await group.next() ?? nil
        group.cancelAll()
        return r
    }
}

func registerServiceChecks(_ r: CheckRunner) {

    let base = URL(string: "http://127.0.0.1:9119/api/plugins/echo_signals")!

    // MARK: GatewayClient 请求/响应配对

    r.checkAsync("GatewayClient: call→response round-trip") {
        let mock = MockGatewayTransport()
        let client = GatewayClient()
        await client.connect(mock)

        let callTask = Task { try await client.createSession() }

        // 等出站请求落地
        var sent: [Data] = []
        for _ in 0..<200 {
            sent = await mock.sentFrames()
            if !sent.isEmpty { break }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        try r.expectTrue(!sent.isEmpty, "no request sent")

        struct Head: Decodable { let id: Int; let method: String }
        let head = try JSONDecoder().decode(Head.self, from: sent[0])
        try r.expect(head.method, "session.create")

        let resp = """
        {"jsonrpc":"2.0","id":\(head.id),"result":{"session_id":"new1","info":{"model":"m","skills":{},"tools":{}}}}
        """
        await mock.injectString(resp)

        let result = try await callTask.value
        try r.expect(result.sessionId, "new1")
        await client.disconnect()
    }

    r.checkAsync("GatewayClient: id increments per call") {
        let mock = MockGatewayTransport()
        let client = GatewayClient()
        await client.connect(mock)
        let id0 = await client.currentNextId
        let t1 = Task { try? await client.submitPrompt(session: "s", text: "a") }
        let t2 = Task { try? await client.submitPrompt(session: "s", text: "b") }
        // 等两条请求都发出
        for _ in 0..<200 {
            if await mock.sentFrames().count >= 2 { break }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        try r.expect(await mock.sentFrames().count, 2)
        try r.expectTrue(await client.currentNextId >= id0 + 2, "id did not advance")
        t1.cancel(); t2.cancel()
        await client.disconnect()
    }

    r.checkAsync("GatewayClient: gateway.ready sets state") {
        let mock = MockGatewayTransport()
        let client = GatewayClient()
        await client.connect(mock)
        await mock.injectString(#"{"jsonrpc":"2.0","method":"event","params":{"type":"gateway.ready","payload":{}}}"#)
        var ready = false
        for _ in 0..<200 {
            if await client.state == .ready { ready = true; break }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        try r.expectTrue(ready, "state never became ready")
        await client.disconnect()
    }

    r.checkAsync("GatewayClient: event stream delivers message.delta") {
        let mock = MockGatewayTransport()
        let client = GatewayClient()
        await client.connect(mock)
        await mock.injectString(#"{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta","session_id":"s1","payload":{"text":"Bosque"}}}"#)
        let ev = await nextEvent(client.events)
        guard let ev else { throw CheckError("no event delivered") }
        try r.expect(ev.sid, "s1")
        guard case .messageDelta(let d) = ev.event else { throw CheckError("not delta: \(ev.event)") }
        try r.expect(d.text, "Bosque")
        await client.disconnect()
    }

    r.checkAsync("GatewayClient: disconnect fails pending calls") {
        let mock = MockGatewayTransport()
        let client = GatewayClient()
        await client.connect(mock)
        let callTask = Task { () -> Bool in
            do { _ = try await client.createSession(); return false }
            catch { return true }  // 期望抛错
        }
        // 等请求发出再断开
        for _ in 0..<200 {
            if await mock.sentFrames().count >= 1 { break }
            try await Task.sleep(nanoseconds: 1_000_000)
        }
        await client.disconnect()
        let threw = await callTask.value
        try r.expectTrue(threw, "pending call did not fail on disconnect")
    }

    // MARK: EchoAPIClient URL / body 构造

    r.checkAsync("EchoAPIClient: recentInvocations URL+query") {
        let rec = RequestRecorder()
        let client = EchoAPIClient(base: base) { req in
            await rec.set(req)
            return (Data("[]".utf8), http200(req.url!))
        }
        _ = try await client.recentInvocations(sessionId: "s1", limit: 3)
        let url = await rec.last!.url!.absoluteString
        try r.expectTrue(url.contains("/invocations/recent"), url)
        try r.expectTrue(url.contains("session_id=s1"), url)
        try r.expectTrue(url.contains("limit=3"), url)
    }

    r.checkAsync("EchoAPIClient: feedback POST body snake_case") {
        let rec = RequestRecorder()
        let client = EchoAPIClient(base: base) { req in
            await rec.set(req)
            return (Data("{}".utf8), http200(req.url!))
        }
        try await client.sendFeedback(FeedbackBody(invocationId: 9, rating: -1, reason: "bad", sessionId: "s1"))
        let req = await rec.last!
        try r.expect(req.httpMethod, "POST")
        let body = String(decoding: req.httpBody ?? Data(), as: UTF8.self)
        try r.expectTrue(body.contains("\"invocation_id\":9"), body)
        try r.expectTrue(body.contains("\"session_id\":\"s1\""), body)
    }

    r.checkAsync("EchoAPIClient: deletePreference uses DELETE") {
        let rec = RequestRecorder()
        let client = EchoAPIClient(base: base) { req in
            await rec.set(req)
            return (Data().isEmpty ? Data("{}".utf8) : Data(), http200(req.url!))
        }
        try await client.deletePreference(7)
        let req = await rec.last!
        try r.expect(req.httpMethod, "DELETE")
        try r.expectTrue(req.url!.absoluteString.hasSuffix("/preferences/7"), req.url!.absoluteString)
    }

    r.checkAsync("EchoAPIClient: non-2xx throws") {
        let client = EchoAPIClient(base: base) { req in
            (Data("{}".utf8), HTTPURLResponse(url: req.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!)
        }
        var threw = false
        do { _ = try await client.status() } catch { threw = true }
        try r.expectTrue(threw, "500 did not throw")
    }
}
