// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import { estimatePromptTokens, promptCharCount, renderPromptHtml } from "@/domain/agent/promptPreview";

const html = (raw: string) => renderPromptHtml(raw).__html;

describe("renderPromptHtml", () => {
  it("renders markdown structure and preserves hard line breaks", () => {
    const result = html("## 输出要求\n1) 只输出 JSON\n2) 不要解释");
    expect(result).toContain("<h2>输出要求</h2>");
    expect(result).toContain("<li>只输出 JSON</li>");
    expect(result).toContain("<li>不要解释</li>");
  });

  it("strips dangerous markup and protocols", () => {
    const result = html("正常文本<script>alert(1)</script><img src=x onerror=alert(1)>\n[bad](javascript:alert(1))");
    expect(result).not.toContain("<script");
    expect(result).not.toContain("onerror");
    expect(result).not.toContain("javascript:");
    expect(result).toContain("正常文本");
  });

  it("replaces markdown images with a text placeholder instead of loading them", () => {
    const result = html("看这个 ![产品图](https://tracker.example/p.png)");
    expect(result).not.toContain("<img");
    expect(result).toContain("[图片：产品图]");
  });

  it("opens links in a new tab", () => {
    const result = html("[文档](https://example.com/doc)");
    expect(result).toContain('target="_blank"');
    expect(result).toContain('rel="noopener noreferrer"');
  });
});

describe("prompt size hints", () => {
  it("counts characters and estimates CJK-aware tokens", () => {
    expect(promptCharCount("abcd")).toBe(4);
    expect(promptCharCount(undefined)).toBe(0);
    expect(estimatePromptTokens("你好世界")).toBe(4);
    expect(estimatePromptTokens("abcdefgh")).toBe(2);
  });
});
