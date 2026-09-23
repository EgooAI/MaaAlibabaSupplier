import { describe, expect, it } from "vitest";
import { getCardStatusLabel } from "@/domain/cards/cardModel";
import { stageLabel, statusLabel } from "@/domain/chat/chatModel";
import { formatDateTime, formatMonthDay } from "@/domain/time";

describe("label fallbacks do not crash on unknown backend values", () => {
  it("returns raw status when unknown", () => {
    expect(statusLabel("some-new-status")).toBe("some-new-status");
    expect(stageLabel("some-new-stage")).toBe("some-new-stage");
    expect(getCardStatusLabel("some-new-status")).toBe("some-new-status");
  });
});

describe("fixed time formatting without locale drift", () => {
  it("handles nullish and unknown inputs without crashing", () => {
    expect(formatDateTime(null)).toBe("未知时间");
    expect(formatDateTime(undefined)).toBe("未知时间");
    expect(formatDateTime("未知时间")).toBe("未知时间");
  });

  it("round-trips wall-time strings without timezone conversion", () => {
    expect(formatDateTime("2026-09-11 08:30")).toBe("2026-09-11 08:30");
    expect(formatMonthDay("2026-09-11 08:30")).toBe("09-11 08:30");
  });
});
