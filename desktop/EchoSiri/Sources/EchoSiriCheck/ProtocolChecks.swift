import Foundation
import EchoSiriKit

// 协议层解码自检：用真实形状的 JSON 帧（对照 gatewayTypes.ts / server.py / plugin_api.py）
// 断言每个 Codable 正确解码、事件分类器正确路由。

func registerProtocolChecks(_ r: CheckRunner) {

    func data(_ s: String) -> Data { Data(s.utf8) }

    // MARK: 帧分类

    r.check("classify: response frame") {
        let f = GatewayDecoder.classify(data(#"{"jsonrpc":"2.0","id":7,"result":{"ok":true}}"#))
        guard case .response(let id) = f else { throw CheckError("not a response: \(f)") }
        try r.expect(id, 7)
    }

    r.check("classify: event frame (real shape: type + session_id + session_key)") {
        let f = GatewayDecoder.classify(data(#"{"jsonrpc":"2.0","method":"event","params":{"type":"message.start","session_id":"s1","session_key":"hermes-42"}}"#))
        guard case .event(let meta) = f else { throw CheckError("not an event: \(f)") }
        try r.expect(meta.event, "message.start")
        try r.expect(meta.sid, "s1")
        try r.expect(meta.sessionKey, "hermes-42")
    }

    // MARK: 会话方法响应

    r.check("decode: session.create response") {
        let json = #"""
        {"jsonrpc":"2.0","id":1,"result":{"session_id":"abc","info":{"model":"qwen-plus","skills":{},"tools":{},"version":"0.14.0"}}}
        """#
        let resp = try GatewayDecoder.decodeResponse(SessionCreateResponse.self, from: data(json))
        try r.expect(resp.result?.sessionId, "abc")
        try r.expect(resp.result?.info?.model, "qwen-plus")
    }

    r.check("decode: session.list response") {
        let json = #"""
        {"jsonrpc":"2.0","id":2,"result":{"sessions":[
          {"id":"s1","title":"Mexico City Largest Park","preview":"What's the largest…","message_count":4,"started_at":1700000000.0,"source":"tui"},
          {"id":"s2","title":"Healthy Recipes","preview":"You can prepare…","message_count":2,"started_at":1699999000.0}
        ]}}
        """#
        let resp = try GatewayDecoder.decodeResponse(SessionListResponse.self, from: data(json))
        try r.expect(resp.result?.sessions?.count, 2)
        try r.expect(resp.result?.sessions?.first?.title, "Mexico City Largest Park")
        try r.expect(resp.result?.sessions?.first?.messageCount, 4)
        try r.expect(resp.result?.sessions?[1].source, nil)  // 缺省字段可选
    }

    r.check("decode: session.resume response") {
        let json = #"""
        {"jsonrpc":"2.0","id":3,"result":{"session_id":"s1","message_count":2,"messages":[
          {"role":"user","text":"hi"},
          {"role":"assistant","text":"hello","name":"qwen"}
        ]}}
        """#
        let resp = try GatewayDecoder.decodeResponse(SessionResumeResponse.self, from: data(json))
        try r.expect(resp.result?.messages.count, 2)
        try r.expect(resp.result?.messages.first?.role, .user)
        try r.expect(resp.result?.messages[1].role, .assistant)
    }

    // MARK: 流式事件

    r.check("parse event: message.delta (nested payload)") {
        let raw = data(#"{"jsonrpc":"2.0","method":"event","params":{"type":"message.delta","session_id":"s1","payload":{"text":"Bosque","rendered":"Bosque"}}}"#)
        guard case .event(let meta) = GatewayDecoder.classify(raw) else { throw CheckError("not event") }
        let pe = EventParser.parse(meta: meta, data: raw)
        try r.expect(pe.sid, "s1")
        guard case .messageDelta(let d) = pe.event else { throw CheckError("not messageDelta: \(pe.event)") }
        try r.expect(d.text, "Bosque")
    }

    r.check("parse event: message.complete with usage") {
        let raw = data(#"""
        {"jsonrpc":"2.0","method":"event","params":{"type":"message.complete","session_id":"s1","payload":{"text":"full answer","status":"complete","usage":{"input":10,"output":20,"total":30,"calls":1,"cost_usd":0.001}}}}
        """#)
        guard case .event(let meta) = GatewayDecoder.classify(raw) else { throw CheckError("not event") }
        let pe = EventParser.parse(meta: meta, data: raw)
        guard case .messageComplete(let c) = pe.event else { throw CheckError("not complete: \(pe.event)") }
        try r.expect(c.text, "full answer")
        try r.expect(c.status, "complete")
        try r.expect(c.usage?.total, 30)
    }

    r.check("parse event: tool.complete (nested payload)") {
        let raw = data(#"{"jsonrpc":"2.0","method":"event","params":{"type":"tool.complete","session_id":"s1","payload":{"tool_id":"t9","name":"read_file","summary":"opened","duration_s":0.4}}}"#)
        guard case .event(let meta) = GatewayDecoder.classify(raw) else { throw CheckError("not event") }
        guard case .toolComplete(let t) = EventParser.parse(meta: meta, data: raw).event else { throw CheckError("not toolComplete") }
        try r.expect(t.name, "read_file")
        try r.expect(t.durationS, 0.4)
    }

    r.check("parse event: clarify.request (M1 nomination)") {
        let raw = data(#"""
        {"jsonrpc":"2.0","method":"event","params":{"type":"clarify.request","session_id":"s1","payload":{"question":"存成技能?","choices":["好","不用"],"request_id":"req42"}}}
        """#)
        guard case .event(let meta) = GatewayDecoder.classify(raw) else { throw CheckError("not event") }
        guard case .clarifyRequest(let c) = EventParser.parse(meta: meta, data: raw).event else { throw CheckError("not clarify") }
        try r.expect(c.question, "存成技能?")
        try r.expect(c.choices.count, 2)
        try r.expect(c.requestId, "req42")
    }

    r.check("parse event: no-payload event (reasoning.available)") {
        let raw = data(#"{"jsonrpc":"2.0","method":"event","params":{"type":"reasoning.available","session_id":"s1"}}"#)
        guard case .event(let meta) = GatewayDecoder.classify(raw) else { throw CheckError("not event") }
        guard case .reasoningAvailable = EventParser.parse(meta: meta, data: raw).event else { throw CheckError("not reasoningAvailable") }
    }

    r.check("parse event: unknown → .other (no frame loss)") {
        let raw = data(#"{"jsonrpc":"2.0","method":"event","params":{"type":"voice.transcript","session_id":"s1","payload":{"text":"hi"}}}"#)
        guard case .event(let meta) = GatewayDecoder.classify(raw) else { throw CheckError("not event") }
        guard case .other(let n) = EventParser.parse(meta: meta, data: raw).event else { throw CheckError("expected .other") }
        try r.expect(n, "voice.transcript")
    }

    // MARK: Echo REST 模型

    r.check("decode: Echo invocations") {
        let json = #"[{"id":12,"skill_id":"ascii-art","skill_name":"ASCII Art","rated":false,"session_id":"s1"}]"#
        let inv = try GatewayDecoder.json.decode([EchoInvocation].self, from: data(json))
        try r.expect(inv.first?.skillId, "ascii-art")
        try r.expect(inv.first?.rated, false)
    }

    r.check("decode: Echo scope pending") {
        let json = #"[{"skill_id":"rename-batch","skill_name":"Batch Rename","session_id":"s1"}]"#
        let sp = try GatewayDecoder.json.decode([ScopePending].self, from: data(json))
        try r.expect(sp.first?.skillId, "rename-batch")
    }

    r.check("decode: Echo skill confidence ranking") {
        let json = #"[{"skill_id":"ascii-art","skill_name":"ASCII","confidence":0.42,"status":"pending_review","n_signals":7}]"#
        let sk = try GatewayDecoder.json.decode([SkillConfidence].self, from: data(json))
        try r.expect(sk.first?.confidence, 0.42)
        try r.expect(sk.first?.status, "pending_review")
    }

    r.check("decode: Echo status") {
        let json = #"{"schema_version":8,"encoder":"neural","table_rows":{"echo_signal_event":120}}"#
        let st = try GatewayDecoder.json.decode(EchoStatus.self, from: data(json))
        try r.expect(st.schemaVersion, 8)
        try r.expect(st.encoder, "neural")
        try r.expect(st.tableRows?["echo_signal_event"], 120)
    }

    // MARK: 出站 body 编码（snake_case)

    r.check("encode: FeedbackBody snake_case") {
        let body = FeedbackBody(invocationId: 12, rating: 1, reason: "great", sessionId: "s1")
        let out = String(decoding: try JSONEncoder().encode(body), as: UTF8.self)
        try r.expectTrue(out.contains("\"invocation_id\":12"), out)
        try r.expectTrue(out.contains("\"session_id\":\"s1\""), out)
    }

    r.check("encode: RPCRequest envelope") {
        struct P: Encodable { let session_id: String; let text: String }
        let req = RPCRequest(id: 5, method: "prompt.submit", params: P(session_id: "s1", text: "hi"))
        let out = String(decoding: try JSONEncoder().encode(req), as: UTF8.self)
        try r.expectTrue(out.contains("\"method\":\"prompt.submit\""), out)
        try r.expectTrue(out.contains("\"jsonrpc\":\"2.0\""), out)
    }
}
