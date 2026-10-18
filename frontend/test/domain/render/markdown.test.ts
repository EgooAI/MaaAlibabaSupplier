// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { renderContentHtml } from "@/domain/render/markdown";

const markdown = (raw: string | null | undefined) => renderContentHtml(raw, "markdown").__html;
const richText = (raw: string | null | undefined) => renderContentHtml(raw, "rich-text").__html;

describe("markdown content", () => {
  it("renders markdown structure and preserves hard line breaks", () => {
    const result = markdown("## 输出要求\n1) 只输出 JSON\n2) 不要解释");
    expect(result).toContain("<h2>输出要求</h2>");
    expect(result).toContain("<li>只输出 JSON</li>");
    expect(result).toContain("<li>不要解释</li>");
  });

  it("strips dangerous markup and protocols", () => {
    const result = markdown("正常文本<script>alert(1)</script><img src=x onerror=alert(1)>\n[bad](javascript:alert(1))");
    expect(result).not.toContain("<script");
    expect(result).not.toContain("onerror");
    expect(result).not.toContain("javascript:");
    expect(result).toContain("正常文本");
  });

  it("replaces markdown images with a text placeholder instead of loading them", () => {
    const result = markdown("看这个 ![产品图](https://tracker.example/p.png)");
    expect(result).not.toContain("<img");
    expect(result).toContain("[图片：产品图]");
  });

  it("opens links in a new tab", () => {
    const result = markdown("[文档](https://example.com/doc)");
    expect(result).toContain('target="_blank"');
    expect(result).toContain('rel="noopener noreferrer"');
  });
});

describe("rich text content", () => {
  it("keeps buyer markup and line breaks", () => {
    expect(richText("a<br>b")).toBe("a<br>b");
    expect(richText('<b>bold</b> and <a href="https://example.com">link</a>')).toContain("<b>bold</b>");
    expect(richText('<a href="https://example.com">link</a>')).toContain('target="_blank"');
    expect(richText("I <3 this")).toContain("&lt;3");
  });

  it("never interprets markdown syntax", () => {
    // Buyer text is markup, not Markdown: a dash list must stay literal.
    expect(richText("- 需要报价")).toBe("- 需要报价");
    expect(richText("# 标题")).toBe("# 标题");
  });

  it("strips executable markup and replaces images", () => {
    expect(richText("x<script>alert(1)</script>y")).toBe("xy");
    expect(richText("<b onclick=alert(1)>b</b>")).not.toContain("onclick");
    expect(richText('<a href="javascript:alert(1)">click</a>')).not.toContain("javascript:");
    expect(richText('see <img src="https://example.com/e.png"> this')).toContain("[图片]");
    expect(richText('see <img src="https://example.com/e.png"> this')).not.toContain("<img");
  });

  it("handles empty input", () => {
    expect(richText("")).toBe("");
    expect(richText(null)).toBe("");
    expect(richText(undefined)).toBe("");
  });
});
