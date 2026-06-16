import Foundation

/// 定位 Echo/Hermes 后端：仓库根 + Python 解释器。
/// 解析顺序对齐 ui-tui/src/gatewayClient.ts 的 resolvePython 与 ./echo 启动器。
public struct BackendLocator: Sendable {

    public struct Resolved: Sendable {
        public let repoRoot: String
        public let python: String
    }

    /// 候选仓库根：env 覆盖 → 应用旁 → 常见开发路径。
    public static func resolveRepoRoot(explicit: String? = nil) -> String? {
        let fm = FileManager.default
        func looksLikeRepo(_ path: String) -> Bool {
            fm.fileExists(atPath: path + "/tui_gateway/entry.py")
        }
        var candidates: [String] = []
        if let explicit { candidates.append(explicit) }
        if let env = ProcessInfo.processInfo.environment["ECHO_REPO_ROOT"] { candidates.append(env) }
        if let env = ProcessInfo.processInfo.environment["HERMES_PYTHON_SRC_ROOT"] { candidates.append(env) }
        // 应用 bundle 旁（分发场景可把后端打进 Resources）
        candidates.append(Bundle.main.bundlePath + "/Contents/Resources/backend")
        return candidates.first(where: looksLikeRepo)
    }

    /// 解析 Python 解释器：env 覆盖 → repo 下 venv → 系统 python3。
    public static func resolvePython(repoRoot: String) -> String {
        let fm = FileManager.default
        if let env = ProcessInfo.processInfo.environment["HERMES_PYTHON"],
           fm.isExecutableFile(atPath: env) { return env }
        let venvCandidates = [
            repoRoot + "/.venv/bin/python",
            repoRoot + "/.venv/bin/python3",
            repoRoot + "/venv/bin/python",
            repoRoot + "/venv/bin/python3",
        ]
        if let hit = venvCandidates.first(where: { fm.isExecutableFile(atPath: $0) }) { return hit }
        if let venv = ProcessInfo.processInfo.environment["VIRTUAL_ENV"] {
            let p = venv + "/bin/python"
            if fm.isExecutableFile(atPath: p) { return p }
        }
        // 常见系统位置兜底
        for p in ["/opt/homebrew/bin/python3", "/usr/bin/python3", "/usr/local/bin/python3"]
        where fm.isExecutableFile(atPath: p) { return p }
        return "python3"
    }

    /// 一步解析。失败返回 nil（找不到后端仓库）。
    public static func resolve(explicitRepoRoot: String? = nil) -> Resolved? {
        guard let root = resolveRepoRoot(explicit: explicitRepoRoot) else { return nil }
        return Resolved(repoRoot: root, python: resolvePython(repoRoot: root))
    }

    /// 探活 dashboard REST（Echo 信号端点）。
    public static func dashboardBase(host: String = "127.0.0.1", port: Int = 9119) -> URL {
        URL(string: "http://\(host):\(port)/api/plugins/echo_signals")!
    }
}
