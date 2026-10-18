import { describe, expect, it } from "vitest";
import { estimatePromptTokens, promptCharCount } from "@/domain/agent/promptPreview";

describe("prompt size hints", () => {
  it("counts characters and estimates CJK-aware tokens", () => {
    expect(promptCharCount("abcd")).toBe(4);
    expect(promptCharCount(undefined)).toBe(0);
    expect(estimatePromptTokens("你好世界")).toBe(4);
    expect(estimatePromptTokens("abcdefgh")).toBe(2);
  });
});
