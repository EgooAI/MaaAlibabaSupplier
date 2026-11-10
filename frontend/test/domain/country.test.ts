import { describe, expect, it } from "vitest";
import { countryCodeOf, countryNameOf } from "@/domain/country";

describe("countryCodeOf", () => {
  it.each([
    ["ID", "ID"],
    ["id", "ID"],
    ["  de  ", "DE"],
    ["Indonesia", "ID"],
    ["Spain", "ES"],
    ["UAE", "AE"],
    ["UK", "GB"],
    ["USA", "US"],
    ["Korea", "KR"],
    ["KSA", "SA"],
    ["Hong Kong", "HK"],
    ["Côte d'Ivoire", "CI"],
    ["Türkiye", "TR"],
    ["阿联酋", "AE"],
  ])("resolves %j to %s", (input, expected) => {
    expect(countryCodeOf(input)).toBe(expected);
  });

  it.each([undefined, "", "  ", "XX", "Straße", "Europe"])("does not invent a code for %j", (input) => {
    expect(countryCodeOf(input)).toBeUndefined();
  });
});

describe("countryNameOf", () => {
  it("returns the Chinese name for a valid code", () => {
    expect(countryNameOf("ID")).toBe("印度尼西亚");
    expect(countryNameOf("id")).toBe("印度尼西亚");
  });

  it.each([undefined, "", "XX"])("returns undefined for %j", (code) => {
    expect(countryNameOf(code)).toBeUndefined();
  });
});
