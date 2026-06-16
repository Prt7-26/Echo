import Foundation

/// 助手回复的块级结构（Kit 层，纯逻辑可自检；UI 层再映射成 ResponseBlock）。
/// 借鉴 macai MessageParser 的「按块切分」思路，自行实现（不依赖其代码）。
public enum MarkdownBlock: Equatable, Sendable {
    case paragraph(String)
    case heading(level: Int, text: String)
    case bullets([String])
    case code(language: String, text: String)
}

public enum MarkdownParser {

    /// 把 Markdown 文本切成块。支持：ATX 标题(#…)、围栏代码块(``` )、
    /// 无序列表(- * • + / 数字.)、空行分段的普通段落。
    public static func parse(_ text: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")

        var i = 0
        var paragraph: [String] = []
        var bullets: [String] = []

        func flushParagraph() {
            if !paragraph.isEmpty {
                let joined = paragraph.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
                if !joined.isEmpty { blocks.append(.paragraph(joined)) }
                paragraph.removeAll()
            }
        }
        func flushBullets() {
            if !bullets.isEmpty { blocks.append(.bullets(bullets)); bullets.removeAll() }
        }

        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // 围栏代码块
            if trimmed.hasPrefix("```") {
                flushParagraph(); flushBullets()
                let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                var body: [String] = []
                i += 1
                while i < lines.count && !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    body.append(lines[i]); i += 1
                }
                i += 1 // 跳过收尾 ```
                blocks.append(.code(language: lang, text: body.joined(separator: "\n")))
                continue
            }

            // ATX 标题
            if let h = atxHeading(trimmed) {
                flushParagraph(); flushBullets()
                blocks.append(.heading(level: h.level, text: h.text))
                i += 1; continue
            }

            // 无序/有序列表项
            if let item = listItem(trimmed) {
                flushParagraph()
                bullets.append(item)
                i += 1; continue
            }

            // 空行 → 段落/列表边界
            if trimmed.isEmpty {
                flushParagraph(); flushBullets()
                i += 1; continue
            }

            // 普通段落行
            flushBullets()
            paragraph.append(trimmed)
            i += 1
        }
        flushParagraph(); flushBullets()
        return blocks
    }

    private static func atxHeading(_ s: String) -> (level: Int, text: String)? {
        guard s.hasPrefix("#") else { return nil }
        var level = 0
        for ch in s { if ch == "#" { level += 1 } else { break } }
        guard level >= 1, level <= 6 else { return nil }
        let rest = String(s.dropFirst(level))
        guard rest.first == " " || rest.isEmpty else { return nil } // "#tag" 不是标题
        return (level, rest.trimmingCharacters(in: .whitespaces))
    }

    private static func listItem(_ s: String) -> String? {
        for marker in ["- ", "* ", "• ", "+ "] where s.hasPrefix(marker) {
            return String(s.dropFirst(marker.count)).trimmingCharacters(in: .whitespaces)
        }
        // 有序列表 "1. " / "12) "
        var idx = s.startIndex
        var digits = 0
        while idx < s.endIndex, s[idx].isNumber { idx = s.index(after: idx); digits += 1 }
        if digits > 0, idx < s.endIndex, s[idx] == "." || s[idx] == ")" {
            let after = s.index(after: idx)
            if after < s.endIndex, s[after] == " " {
                return String(s[s.index(after: after)...]).trimmingCharacters(in: .whitespaces)
            }
        }
        return nil
    }
}
