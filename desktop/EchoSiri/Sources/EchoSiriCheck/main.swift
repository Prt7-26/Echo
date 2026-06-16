import Foundation
import EchoSiriKit

// 自检入口。新增协议层逻辑时在这里追加 runner.check(...)。
let runner = CheckRunner()

print("EchoSiri self-check (EchoSiriKit \(EchoSiriKit.version))\n")

runner.check("kit version is set") {
    try runner.expect(EchoSiriKit.version, "0.0.1")
}

registerProtocolChecks(runner)

runner.run()
