import Foundation

// 把一帧事件（已知 EventMeta + 原始 Data）解析为强类型 ParsedEvent。
// payload 字段是 params 里 event/sid 的兄弟键，按事件名各取所需。

public enum EventParser {

    /// 解析事件帧。永不抛错：未知事件或 payload 解码失败时降级为 `.other`，不丢帧。
    public static func parse(meta: EventMeta, data: Data) -> ParsedEvent {
        let event = decode(name: meta.event, data: data)
        return ParsedEvent(sid: meta.sid, sessionKey: meta.sessionKey, event: event)
    }

    private static func decode(name: String, data: Data) -> GatewayEvent {
        func payload<P: Decodable>(_ type: P.Type) -> P? {
            (try? GatewayDecoder.decodeEventPayload(type, from: data)) ?? nil
        }
        switch name {
        case GatewayEventName.ready:
            return .ready
        case GatewayEventName.sessionInfo:
            // session.info 的 payload 直接就是 SessionInfo 的字段
            if let info = payload(SessionInfo.self) { return .sessionInfo(info) }
            return .other(name: name)
        case GatewayEventName.messageStart:
            return .messageStart
        case GatewayEventName.messageDelta:
            if let p = payload(MessageDelta.self) { return .messageDelta(p) }
            return .other(name: name)
        case GatewayEventName.messageComplete:
            if let p = payload(MessageComplete.self) { return .messageComplete(p) }
            return .other(name: name)
        case GatewayEventName.toolGenerating:
            if let p = payload(ToolGenerating.self) { return .toolGenerating(p) }
            return .other(name: name)
        case GatewayEventName.toolProgress:
            if let p = payload(ToolProgress.self) { return .toolProgress(p) }
            return .other(name: name)
        case GatewayEventName.toolComplete:
            if let p = payload(ToolComplete.self) { return .toolComplete(p) }
            return .other(name: name)
        case GatewayEventName.reasoningDelta:
            if let p = payload(TextDelta.self) { return .reasoningDelta(p) }
            return .other(name: name)
        case GatewayEventName.thinkingDelta:
            if let p = payload(TextDelta.self) { return .thinkingDelta(p) }
            return .other(name: name)
        case GatewayEventName.reasoningAvailable:
            return .reasoningAvailable
        case GatewayEventName.statusUpdate:
            if let p = payload(StatusUpdate.self) { return .statusUpdate(p) }
            return .other(name: name)
        case GatewayEventName.clarifyRequest:
            if let p = payload(ClarifyRequest.self) { return .clarifyRequest(p) }
            return .other(name: name)
        case GatewayEventName.approvalRequest:
            if let p = payload(ApprovalRequest.self) { return .approvalRequest(p) }
            return .other(name: name)
        case GatewayEventName.secretRequest:
            if let p = payload(SecretRequest.self) { return .secretRequest(p) }
            return .other(name: name)
        case GatewayEventName.error:
            if let p = payload(ErrorEvent.self) { return .error(p) }
            return .other(name: name)
        default:
            return .other(name: name)
        }
    }
}
