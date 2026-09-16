import { describe, expect, it } from "vitest";
import { renderMessageHtml } from "@/domain/chat/messageHtml";

function html(raw: string | null | undefined) {
  return renderMessageHtml(raw).__html;
}

describe("renderMessageHtml", () => {
  it("keeps plain text as-is", () => {
    expect(html("Hello world")).toBe("Hello world");
  });

  it("preserves line breaks and formatting tags", () => {
    expect(html("a<br>b")).toBe("a<br>b");
    expect(html("<b>bold</b> and <a href=\"https://example.com\">link</a>")).toContain("<b>bold</b>");
    expect(html("<a href=\"https://example.com\">link</a>")).toContain("target=\"_blank\"");
    expect(html("<a href=\"https://example.com\">link</a>")).toContain("noopener");
  });

  it("strips executable markup", () => {
    expect(html("x<script>alert(1)</script>y")).toBe("xy");
    expect(html("<img src=x onerror=alert(1)>")).not.toContain("onerror");
    expect(html("<b onclick=alert(1)>b</b>")).not.toContain("onclick");
    expect(html("<a href=\"javascript:alert(1)\">click</a>")).not.toContain("javascript:");
  });

  it("replaces images with a placeholder", () => {
    expect(html("see <img src=\"https://example.com/e.png\"> this")).toContain("[图片]");
    expect(html("see <img src=\"https://example.com/e.png\"> this")).not.toContain("<img");
  });

  it("keeps text that merely looks like markup (escaped, renders back as-is)", () => {
    expect(html("I <3 this")).toContain("&lt;3");
    expect(html("I <3 this")).not.toContain("<3 this");
  });

  it("handles empty input", () => {
    expect(html("")).toBe("");
    expect(html(null)).toBe("");
    expect(html(undefined)).toBe("");
  });
});
