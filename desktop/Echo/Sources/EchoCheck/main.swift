import Foundation
import EchoKit

// 自检入口。新增协议层逻辑时在这里追加 runner.check(...)。
let runner = CheckRunner()

print("Echo self-check (EchoKit \(EchoKit.version))\n")

runner.check("kit version is set") {
    try runner.expect(EchoKit.version, "0.0.1")
}

registerProtocolChecks(runner)
registerMarkdownChecks(runner)
registerServiceChecks(runner)
registerLiveChecks(runner)   // 仅 ECHO_APP_LIVE=1 时挂载

await runner.run()
