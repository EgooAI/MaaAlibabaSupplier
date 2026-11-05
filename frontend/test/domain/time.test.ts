import { describe, expect, it } from "vitest";
import { formatFullDateTime, formatSmartTime } from "@/domain/time";

// Local wall clock, so the cases do not depend on the machine timezone.
const THURSDAY = new Date(2026, 10, 5, 12, 0, 0);

const at = (secondsFromNow: number) => new Date(THURSDAY.getTime() + secondsFromNow * 1000).toISOString();
const epoch = (secondsFromNow: number) => Math.floor((THURSDAY.getTime() + secondsFromNow * 1000) / 1000);

describe("formatSmartTime", () => {
  it("uses relative wording under one hour, from either side of now", () => {
    expect(formatSmartTime(at(-30), THURSDAY)).toBe("刚刚");
    expect(formatSmartTime(at(-59), THURSDAY)).toBe("刚刚");
    expect(formatSmartTime(at(30), THURSDAY)).toBe("即将");
    expect(formatSmartTime(at(-60), THURSDAY)).toBe("1分钟前");
    expect(formatSmartTime(at(-90), THURSDAY)).toBe("1分钟前");
    expect(formatSmartTime(at(120), THURSDAY)).toBe("2分钟后");
    expect(formatSmartTime(at(-3599), THURSDAY)).toBe("59分钟前");
  });

  it("falls back to the calendar day once the gap reaches an hour", () => {
    expect(formatSmartTime("2026-11-05 11:00", THURSDAY)).toBe("今天 11:00");
    expect(formatSmartTime("2026-11-05 00:05", THURSDAY)).toBe("今天 00:05");
  });

  it("labels the two neighbouring calendar days", () => {
    expect(formatSmartTime("2026-11-04 23:10", THURSDAY)).toBe("昨天 23:10");
    expect(formatSmartTime("2026-11-06 09:05", THURSDAY)).toBe("明天 09:05");
  });

  it("names the weekday only inside the same Monday-based week", () => {
    expect(formatSmartTime("2026-11-02 07:30", THURSDAY)).toBe("星期一 07:30");
    expect(formatSmartTime("2026-11-08 18:00", THURSDAY)).toBe("星期日 18:00");
    expect(formatSmartTime("2026-10-28 07:30", THURSDAY)).toBe("10-28 07:30");
  });

  it("keeps yesterday ahead of the week rule on a Monday", () => {
    const monday = new Date(2026, 10, 2, 12, 0, 0);
    expect(formatSmartTime("2026-11-01 09:00", monday)).toBe("昨天 09:00");
  });

  it("names the weekday for the previous Monday when now is Sunday", () => {
    const sunday = new Date(2026, 10, 8, 12, 0, 0);
    expect(formatSmartTime("2026-11-02 09:00", sunday)).toBe("星期一 09:00");
  });

  it("drops the clock for another year", () => {
    expect(formatSmartTime("2025-12-31 07:30", THURSDAY)).toBe("2025-12-31");
    expect(formatSmartTime("2027-01-02 07:30", THURSDAY)).toBe("2027-01-02");
  });

  it("accepts second timestamps and returns the raw text for unusable input", () => {
    expect(formatSmartTime(epoch(-120), THURSDAY)).toBe("2分钟前");
    expect(formatSmartTime("未知时间", THURSDAY)).toBe("未知时间");
    expect(formatSmartTime("", THURSDAY)).toBe("未知时间");
    expect(formatSmartTime(null, THURSDAY)).toBe("未知时间");
  });
});

describe("formatFullDateTime", () => {
  it("keeps seconds for the hover hint", () => {
    expect(formatFullDateTime("2026-11-05 08:00")).toBe("2026-11-05 08:00:00");
    expect(formatFullDateTime("2026-11-05 08:00:07")).toBe("2026-11-05 08:00:07");
    expect(formatFullDateTime(Math.floor(new Date(2026, 10, 5, 8, 0, 7).getTime() / 1000))).toBe("2026-11-05 08:00:07");
  });

  it("keeps the existing fallbacks", () => {
    expect(formatFullDateTime(null)).toBe("未知时间");
    expect(formatFullDateTime("")).toBe("未知时间");
    expect(formatFullDateTime("未知时间")).toBe("未知时间");
  });
});
