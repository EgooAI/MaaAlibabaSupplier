import { describe, expect, it } from "vitest";
import { localTimeLabel } from "@/domain/localTime";

describe("localTimeLabel", () => {
  it("follows the zone's daylight saving rules", () => {
    expect(localTimeLabel("DE", new Date("2026-01-15T12:00:00Z"))).toBe("当地时间 13:00");
    expect(localTimeLabel("DE", new Date("2026-07-15T12:00:00Z"))).toBe("当地时间 14:00");
    expect(localTimeLabel("pk", new Date("2026-11-05T12:47:00Z"))).toBe("当地时间 17:47");
  });

  it("marks multi-zone countries as approximate", () => {
    expect(localTimeLabel("US", new Date("2026-01-15T12:00:00Z"))).toBe("当地时间约 07:00");
  });

  it.each([undefined, "", "  ", "XX", "Spain"])("shows nothing for unknown country %j", (country) => {
    expect(localTimeLabel(country, new Date("2026-01-15T12:00:00Z"))).toBeUndefined();
  });
});
