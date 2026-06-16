import Foundation
import EchoSiriKit

func registerMarkdownChecks(_ r: CheckRunner) {

    r.check("md: heading + paragraph + bullets") {
        let blocks = MarkdownParser.parse("""
        # Bosque de Chapultepec

        Often called the lungs of the city.

        - A historic castle
        - A major museum
        - A city zoo
        """)
        try r.expect(blocks.count, 3)
        try r.expect(blocks[0], .heading(level: 1, text: "Bosque de Chapultepec"))
        guard case .paragraph(let p) = blocks[1] else { throw CheckError("not paragraph") }
        try r.expectTrue(p.contains("lungs"), p)
        guard case .bullets(let items) = blocks[2] else { throw CheckError("not bullets") }
        try r.expect(items.count, 3)
        try r.expect(items[0], "A historic castle")
    }

    r.check("md: fenced code block") {
        let blocks = MarkdownParser.parse("""
        Here is code:

        ```swift
        let x = 1
        print(x)
        ```
        """)
        try r.expect(blocks.count, 2)
        guard case .code(let lang, let body) = blocks[1] else { throw CheckError("not code: \(blocks)") }
        try r.expect(lang, "swift")
        try r.expectTrue(body.contains("let x = 1"), body)
        try r.expectTrue(body.contains("print(x)"), body)
    }

    r.check("md: numbered list → bullets") {
        let blocks = MarkdownParser.parse("1. first\n2. second\n3) third")
        guard case .bullets(let items) = blocks.first else { throw CheckError("not bullets: \(blocks)") }
        try r.expect(items, ["first", "second", "third"])
    }

    r.check("md: '#tag' is NOT a heading") {
        let blocks = MarkdownParser.parse("#hashtag not a heading")
        guard case .paragraph(let p) = blocks.first else { throw CheckError("expected paragraph") }
        try r.expect(p, "#hashtag not a heading")
    }

    r.check("md: multi-line paragraph joins") {
        let blocks = MarkdownParser.parse("line one\nline two\n\nsecond para")
        try r.expect(blocks.count, 2)
        guard case .paragraph(let p1) = blocks[0] else { throw CheckError("p1") }
        try r.expectTrue(p1.contains("line one") && p1.contains("line two"), p1)
    }

    r.check("md: plain text → single paragraph") {
        let blocks = MarkdownParser.parse("just some plain text")
        try r.expect(blocks.count, 1)
        try r.expect(blocks[0], .paragraph("just some plain text"))
    }
}
