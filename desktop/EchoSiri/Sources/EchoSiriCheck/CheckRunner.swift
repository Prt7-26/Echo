import Foundation

/// 极简自检 harness（CLT 下 XCTest 不可用的替代）。
/// 用法：注册一组 `check("name") { ... }`，`run()` 跑全部、汇总、非零退出码。
final class CheckRunner {
    private var cases: [(name: String, body: () throws -> Void)] = []
    private var failures: [(String, String)] = []
    private var passed = 0

    func check(_ name: String, _ body: @escaping () throws -> Void) {
        cases.append((name, body))
    }

    /// 断言相等（轻量）。失败抛错，由 run() 捕获记录。
    func expect<T: Equatable>(_ actual: T, _ expected: T, _ msg: String = "",
                             file: StaticString = #file, line: UInt = #line) throws {
        if actual != expected {
            throw CheckError("expected \(expected), got \(actual)\(msg.isEmpty ? "" : " — \(msg)")")
        }
    }

    func expectTrue(_ cond: Bool, _ msg: String = "") throws {
        if !cond { throw CheckError("expected true — \(msg)") }
    }

    func run() -> Never {
        for c in cases {
            do {
                try c.body()
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
