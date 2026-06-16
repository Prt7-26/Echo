import Foundation

/// 极简自检 harness（CLT 下 XCTest 不可用的替代）。支持同步与异步用例。
/// 用法：注册 `check("name") { ... }` / `checkAsync("name") { await ... }`，
/// `await run()` 跑全部、汇总、非零退出码。
final class CheckRunner: @unchecked Sendable {
    private var cases: [(name: String, body: () async throws -> Void)] = []
    private var failures: [(String, String)] = []
    private var passed = 0

    func check(_ name: String, _ body: @escaping () throws -> Void) {
        cases.append((name, { try body() }))
    }

    func checkAsync(_ name: String, _ body: @escaping () async throws -> Void) {
        cases.append((name, body))
    }

    func expect<T: Equatable>(_ actual: T, _ expected: T, _ msg: String = "") throws {
        if actual != expected {
            throw CheckError("expected \(expected), got \(actual)\(msg.isEmpty ? "" : " — \(msg)")")
        }
    }

    func expectTrue(_ cond: Bool, _ msg: String = "") throws {
        if !cond { throw CheckError("expected true — \(msg)") }
    }

    func run() async -> Never {
        for c in cases {
            do {
                try await c.body()
                passed += 1
                print("  ✓ \(c.name)")
            } catch {
                failures.append((c.name, "\(error)"))
                print("  ✗ \(c.name): \(error)")
            }
        }
        print("\n\(passed)/\(cases.count) passed, \(failures.count) failed")
        exit(failures.isEmpty ? 0 : 1)
    }
}

struct CheckError: Error, CustomStringConvertible {
    let description: String
    init(_ d: String) { description = d }
}
