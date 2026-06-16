import SwiftUI

/// Echo 品牌标记（波形/脉冲线），sonar-teal 着色。各处统一用它，
/// 改 `Theme.logoSymbol` 即可全局换标。
struct EchoLogo: View {
    var size: CGFloat = 22
    var weight: Font.Weight = .regular
    var color: Color = Theme.accent

    var body: some View {
        Image(systemName: Theme.logoSymbol)
            .font(.system(size: size, weight: weight))
            .foregroundStyle(color)
            .accessibilityLabel("Echo")
    }
}

#if canImport(PreviewsMacros)
#Preview {
    VStack(spacing: 20) {
        EchoLogo(size: 44, weight: .light)
        EchoLogo(size: 22)
        HStack { EchoLogo(size: 15); Text("Echo").font(.headline) }
    }.padding()
}
#endif
