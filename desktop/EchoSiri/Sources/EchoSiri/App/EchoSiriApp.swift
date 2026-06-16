import SwiftUI
import EchoSiriKit

@main
struct EchoSiriApp: App {
    var body: some Scene {
        WindowGroup {
            Text("EchoSiri \(EchoSiriKit.version)")
                .frame(minWidth: 400, minHeight: 300)
        }
    }
}
