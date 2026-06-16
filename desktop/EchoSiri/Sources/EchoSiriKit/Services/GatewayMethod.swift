import Foundation

/// Gateway JSON-RPC 方法名常量。对照 tui_gateway/server.py 的 @method(...) 注册。
public enum GatewayMethod {
    // 会话生命周期
    public static let sessionList = "session.list"
    public static let sessionCreate = "session.create"
    public static let sessionResume = "session.resume"
    public static let sessionClose = "session.close"
    public static let sessionTitle = "session.title"
    public static let sessionDelete = "session.delete"
    public static let sessionBranch = "session.branch"
    public static let sessionHistory = "session.history"
    public static let sessionUsage = "session.usage"
    public static let sessionInterrupt = "session.interrupt"
    public static let sessionSteer = "session.steer"
    public static let sessionUndo = "session.undo"
    public static let sessionCompress = "session.compress"
    // 提交
    public static let promptSubmit = "prompt.submit"
    public static let promptBackground = "prompt.background"
    // 交互应答
    public static let clarifyRespond = "clarify.respond"
    public static let approvalRespond = "approval.respond"
    public static let secretRespond = "secret.respond"
    // 控制
    public static let stop = "stop"
    public static let slashExec = "slash.exec"
}

public enum GatewayError: Error, Equatable {
    case notConnected
    case emptyResult
    case timeout
    case transportClosed
}
