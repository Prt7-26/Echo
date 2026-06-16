import SwiftUI

/// 双列（可配）错落瀑布流布局（线框图 W2）。
/// 贪心：每个子视图放进当前累计高度最矮的那列。子视图高度由内容自定。
struct MasonryLayout: Layout {
    var columns: Int = 2
    var spacing: CGFloat = Tokens.Spacing.cardGutter

    private func columnWidth(for width: CGFloat) -> CGFloat {
        let totalSpacing = spacing * CGFloat(columns - 1)
        return max(0, (width - totalSpacing) / CGFloat(columns))
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 0
        guard width > 0, !subviews.isEmpty else { return .zero }
        let colW = columnWidth(for: width)
        var heights = Array(repeating: CGFloat(0), count: columns)
        for sv in subviews {
            let h = sv.sizeThatFits(.init(width: colW, height: nil)).height
            let c = shortestColumn(heights)
            heights[c] += (heights[c] > 0 ? spacing : 0) + h
        }
        return CGSize(width: width, height: heights.max() ?? 0)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        let colW = columnWidth(for: bounds.width)
        var heights = Array(repeating: CGFloat(0), count: columns)
        for sv in subviews {
            let h = sv.sizeThatFits(.init(width: colW, height: nil)).height
            let c = shortestColumn(heights)
            let x = bounds.minX + CGFloat(c) * (colW + spacing)
            let y = bounds.minY + heights[c] + (heights[c] > 0 ? spacing : 0)
            sv.place(at: CGPoint(x: x, y: y),
                     proposal: .init(width: colW, height: h))
            heights[c] += (heights[c] > 0 ? spacing : 0) + h
        }
    }

    private func shortestColumn(_ heights: [CGFloat]) -> Int {
        var best = 0
        for i in heights.indices where heights[i] < heights[best] { best = i }
        return best
    }
}
