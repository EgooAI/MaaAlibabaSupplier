import { describe, expect, it } from "vitest";
import { buildPromptDiff } from "@/domain/agent/promptDiff";

describe("buildPromptDiff", () => {
  it("marks identical text as context rows", () => {
    const diff = buildPromptDiff("第一行\n第二行", "第一行\n第二行");
    expect(diff.rows.every((row) => row.kind === "context")).toBe(true);
    expect(diff.addedLines).toBe(0);
    expect(diff.removedLines).toBe(0);
  });

  it("pairs changed lines and highlights the changed characters", () => {
    const diff = buildPromptDiff("你是助手。\n只输出 JSON。", "你是助手。\n只输出 JSON，不要解释。");
    expect(diff.rows.map((row) => row.kind)).toEqual(["context", "changed"]);
    const changed = diff.rows[1];
    expect(changed.beforeSegments).toBeDefined();
    expect(changed.afterSegments?.some((segment) => segment.kind === "added" && segment.text.includes("不要解释"))).toBe(true);
    expect(diff.addedLines).toBe(1);
    expect(diff.removedLines).toBe(1);
  });

  it("does not mispair a shared last line that only gained new content", () => {
    const diff = buildPromptDiff("a", "a\nb\nc");
    expect(diff.rows.map((row) => row.kind)).toEqual(["context", "added", "added"]);
    expect(diff.addedLines).toBe(2);
    expect(diff.removedLines).toBe(0);
  });

  it("reports removals without counting the untouched prefix", () => {
    const diff = buildPromptDiff("a\nb\nc", "a");
    expect(diff.rows.map((row) => row.kind)).toEqual(["context", "removed", "removed"]);
    expect(diff.removedLines).toBe(2);
    expect(diff.addedLines).toBe(0);
  });

  it("treats an empty draft as all-new content", () => {
    const diff = buildPromptDiff("", "第一行\n第二行");
    expect(diff.rows.every((row) => row.kind === "added")).toBe(true);
    expect(diff.addedLines).toBe(2);
  });

  it("keeps blank-line additions single and paired", () => {
    const diff = buildPromptDiff("a\n\nb", "a\n\n\nb");
    expect(diff.rows.filter((row) => row.kind === "added")).toHaveLength(1);
    expect(diff.addedLines).toBe(1);
    expect(diff.removedLines).toBe(0);
  });
});
