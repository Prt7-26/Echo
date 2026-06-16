import SwiftUI

// 设计系统：分清 Apple HIG 的两层。
//
// ★ Liquid Glass 只用于「导航/控件层」——浮在内容之上的功能性元素：
//   工具栏按钮(GlassButtonStyle)、浮起输入条(glassPanel)、浮在对话上的瞬时
//   提示卡(glassCard，如评分/scope/clarify)、sidebar 容器(系统材质)。
//   官方原则：Liquid Glass is for the navigation layer that floats above content.
//
// ★ 内容层用实底，绝不用玻璃——会话卡片(列表项)、对话富文本、工具/推理/代码块、
//   来源 chip、侧面板行：contentCard / insetSurface（实底 + 轻投影/淡填充）。
//   官方原则：Don't use Liquid Glass in the content layer (lists/tables/media)。
//
// 玻璃部分唯一一处 `#available(macOS 26, *)` 切换：26+ 真 `.glassEffect`，
// 15 回落 `.regularMaterial` + 描边。见 DevPlan/siri-app-wireframes.md「W8」。

// MARK: - 玻璃（导航/控件层）

extension View {
    /// 浮起玻璃卡（仅用于浮在内容之上的瞬时提示：评分/scope/clarify）。
    func glassCard(cornerRadius: CGFloat = Tokens.Radius.card, tinted: Bool = false) -> some View {
        modifier(GlassCardModifier(cornerRadius: cornerRadius, tinted: tinted))
    }

    /// 浮起玻璃面板（输入条、悬浮容器）。
    func glassPanel(cornerRadius: CGFloat = Tokens.Radius.button) -> some View {
        modifier(GlassPanelModifier(cornerRadius: cornerRadius))
    }
}

// MARK: - 内容层（实底，非玻璃）

extension View {
    /// 内容卡片（会话卡等列表项）：实底 + 轻投影 + 发丝边；选中态 accent 描边。
    func contentCard(cornerRadius: CGFloat = Tokens.Radius.card, selected: Bool = false) -> some View {
        modifier(ContentCardModifier(cornerRadius: cornerRadius, selected: selected))
    }

    /// 内嵌内容面（回复内的工具/推理/代码/来源/侧面板行）：更淡的填充，无投影。
    func insetSurface(cornerRadius: CGFloat = Tokens.Radius.button) -> some View {
        modifier(InsetSurfaceModifier(cornerRadius: cornerRadius))
    }
}

private struct ContentCardModifier: ViewModifier {
    let cornerRadius: CGFloat
    let selected: Bool
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        // 不用 .shadow（每张卡一次离屏栅格化，masonry 滚动时帧率杀手）；
        // 靠 cardSurface 与 sidebar 材质的明度差 + 发丝边做分隔，开销近乎为零。
        content
            .background(Theme.cardSurface, in: shape)
            .overlay(shape.strokeBorder(
                selected ? Theme.accent.opacity(0.85) : Theme.hairline.opacity(0.55),
                lineWidth: selected ? 1.5 : 0.5))
    }
}

private struct InsetSurfaceModifier: ViewModifier {
    let cornerRadius: CGFloat
    func body(content: Content) -> some View {
        content.background(Theme.insetSurface,
                           in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
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

// MARK: - 玻璃按钮（工具栏图标按钮）

/// 常驻、圆润的 Liquid Glass 胶囊按钮（macOS Preview 同款）——
/// 玻璃始终可见(非 hover 才显)，Capsule 全圆角(非方角)。macOS 26 的
/// `.interactive()` 自带 hover/按压高光；15 回落常驻 thinMaterial 胶囊。
struct IconGlassButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        let shape = Capsule(style: .continuous)
        return configuration.label
            .font(.system(size: 14, weight: .medium))
            .frame(minWidth: 30, minHeight: 28)   // 圆润、尺寸一致
            .padding(.horizontal, 8)
            .contentShape(shape)
            .modifier(PersistentGlass(shape: shape))
            .scaleEffect(configuration.isPressed ? 0.95 : 1)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .animation(.easeOut(duration: 0.10), value: configuration.isPressed)
    }
}

/// 常驻玻璃背景（始终可见）。
private struct PersistentGlass: ViewModifier {
    let shape: Capsule
    func body(content: Content) -> some View {
        if #available(macOS 26.0, *) {
            content.glassEffect(.regular.interactive(), in: shape)
        } else {
            content
                .background(.thinMaterial, in: shape)
                .overlay(shape.strokeBorder(.white.opacity(0.12), lineWidth: 0.75))
        }
    }
}

extension ButtonStyle where Self == IconGlassButtonStyle {
    static var glassIcon: IconGlassButtonStyle { IconGlassButtonStyle() }
}

// 窗口级 translucency 由 EchoApp 的 `.containerBackground(.ultraThinMaterial, for: .window)`
// 承载（仅作为窗口 chrome）；内容区一律用 Theme.contentBackground 实底覆盖在其上，
// 避免内容透出材质（HIG：内容层不用 Liquid Glass）。
