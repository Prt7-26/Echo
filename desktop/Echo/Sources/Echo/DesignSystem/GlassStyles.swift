import SwiftUI

// Liquid Glass 封装层。
//
// 唯一一处 `#available(macOS 26, *)` 切换，View 代码不散落可用性判断：
//   • macOS 26+  → 真 Liquid Glass `.glassEffect`
//   • macOS 15   → `.regularMaterial` + 描边 的等效降级
//
// 见 DevPlan/siri-app-wireframes.md「W8 降级对照」。

// MARK: - 玻璃卡片

extension View {
    /// 圆角玻璃卡（侧栏卡片、信号卡、内联图等）。
    func glassCard(cornerRadius: CGFloat = Tokens.Radius.card, tinted: Bool = false) -> some View {
        modifier(GlassCardModifier(cornerRadius: cornerRadius, tinted: tinted))
    }

    /// 浮起玻璃面板（输入条、工具栏、悬浮容器）。
    func glassPanel(cornerRadius: CGFloat = Tokens.Radius.button) -> some View {
        modifier(GlassPanelModifier(cornerRadius: cornerRadius))
    }
}

private struct GlassCardModifier: ViewModifier {
    let cornerRadius: CGFloat
    let tinted: Bool

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        if #available(macOS 26.0, *) {
            content
                .glassEffect(
                    tinted ? .regular.tint(Theme.accent.opacity(0.18)) : .regular,
                    in: shape
                )
        } else {
            content
                .background(.regularMaterial, in: shape)
                .overlay(shape.strokeBorder(.white.opacity(0.10), lineWidth: 0.75))
                .shadow(color: .black.opacity(0.08), radius: 4, y: 1)
        }
    }
}

private struct GlassPanelModifier: ViewModifier {
    let cornerRadius: CGFloat

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        if #available(macOS 26.0, *) {
            content.glassEffect(.regular.interactive(), in: shape)
        } else {
            content
                .background(.ultraThinMaterial, in: shape)
                .overlay(shape.strokeBorder(.white.opacity(0.12), lineWidth: 0.75))
        }
    }
}

// MARK: - 玻璃按钮

/// 无边框玻璃圆角按钮，hover 才显描边（W1/W2 工具栏按钮）。
struct GlassButtonStyle: ButtonStyle {
    var cornerRadius: CGFloat = Tokens.Radius.button
    @State private var hovering = false

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        return configuration.label
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .contentShape(shape)
            .background {
                if #available(macOS 26.0, *) {
                    shape.fill(.clear).glassEffect(.regular.interactive(), in: shape)
                        .opacity(hovering || configuration.isPressed ? 1 : 0.0)
                } else {
                    shape.fill(.ultraThinMaterial)
                        .opacity(hovering || configuration.isPressed ? 1 : 0.0)
                }
            }
            .opacity(configuration.isPressed ? 0.6 : 1)
            .onHover { hovering = $0 }
            .animation(.easeOut(duration: 0.12), value: hovering)
    }
}

extension ButtonStyle where Self == GlassButtonStyle {
    static var glassIcon: GlassButtonStyle { GlassButtonStyle() }
}

// MARK: - 窗口背景

/// 整窗 Liquid Glass 背景：壁纸从边缘透出（W1）。
struct WindowGlassBackground: View {
    var body: some View {
        if #available(macOS 26.0, *) {
            // macOS 26 下窗口本身的玻璃由 .containerBackground/材质承载；
            // 这里铺一层极薄材质兜底，真正的「壁纸延伸」交给窗口层 backgroundExtensionEffect。
            Color.clear.background(.ultraThinMaterial.opacity(0.6))
        } else {
            Color.clear.background(.ultraThinMaterial)
        }
    }
}
