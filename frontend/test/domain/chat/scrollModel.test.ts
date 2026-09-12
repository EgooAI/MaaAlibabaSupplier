import { describe, expect, it } from "vitest";
import { BOTTOM_FOLLOW_THRESHOLD_PX, isNearBottom } from "@/domain/chat/scrollModel";

describe("isNearBottom", () => {
  it("stuck to the bottom counts as near", () => {
    expect(isNearBottom({ scrollTop: 920, scrollHeight: 1000, clientHeight: 80 })).toBe(true);
  });

  it("within the threshold counts as near", () => {
    expect(
      isNearBottom({ scrollTop: 1000 - 200 - BOTTOM_FOLLOW_THRESHOLD_PX, scrollHeight: 1000, clientHeight: 200 }),
    ).toBe(true);
  });

  it("scrolled-up content counts as away", () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 2000, clientHeight: 400 })).toBe(false);
  });

  it("content shorter than the viewport counts as near", () => {
    expect(isNearBottom({ scrollTop: 0, scrollHeight: 300, clientHeight: 400 })).toBe(true);
  });
});
