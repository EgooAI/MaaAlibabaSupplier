import { describe, expect, it } from "vitest";
import { BOTTOM_FOLLOW_THRESHOLD_PX, isNearBottom } from "@/domain/chat/scrollModel";

describe("isNearBottom", () => {
  it.each([
    ["stuck to the bottom", { scrollTop: 920, scrollHeight: 1000, clientHeight: 80 }, true],
    ["within the threshold", { scrollTop: 1000 - 200 - BOTTOM_FOLLOW_THRESHOLD_PX, scrollHeight: 1000, clientHeight: 200 }, true],
    ["scrolled up", { scrollTop: 0, scrollHeight: 2000, clientHeight: 400 }, false],
    ["content shorter than the viewport", { scrollTop: 0, scrollHeight: 300, clientHeight: 400 }, true],
  ] as const)("%s", (_label, metrics, expected) => {
    expect(isNearBottom(metrics)).toBe(expected);
  });
});
