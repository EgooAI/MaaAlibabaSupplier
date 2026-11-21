import { describe, expect, it } from "vitest";
import { customerQualityOf } from "@/domain/chat/customerQuality";
import type { CustomerD90Behavior } from "@/types/chatCanonical";

describe("customer quality index I′", () => {
  it.each([
    [{ productViews: 1, validInquiries: 1, validRfqs: 1, loginDays: 1 }, 94, "S"],
    [{ productViews: 199, validInquiries: 6, validRfqs: 6, loginDays: 78 }, 81, "A"],
    [{ productViews: 39, validInquiries: 7, validRfqs: 6, loginDays: 17 }, 90, "S"],
    [{ productViews: 323, validInquiries: 27, loginDays: 27, blacklisted: 1 }, 75, "B"],
    [{ productViews: 1, validInquiries: 1, validRfqs: 1, loginDays: 1, spamInquiries: 1, blacklisted: 1 }, 81, "A"],
    [{ validRfqs: 1 }, 100, "S"],
    [{ spamInquiries: 255 }, 0, "D"],
  ] as const)("calculates the weighted ratio and combined risk for %j", (behavior, score, grade) => {
    expect(customerQualityOf(behavior)).toEqual({ score, grade });
  });

  it.each([
    [0, 4, 1, 59, "D"],
    [0, 5, 1, 60, "C"],
    [0, 8, 0, 69, "C"],
    [1, 3, 1, 70, "B"],
    [2, 4, 1, 79, "B"],
    [1, 1, 1, 80, "A"],
    [4, 10, 0, 89, "A"],
    [3, 4, 1, 90, "S"],
  ] as const)("grades after rounding for RFQ=%i, login=%i, blacklist=%i", (validRfqs, loginDays, blacklisted, score, grade) => {
    expect(customerQualityOf({ validRfqs, loginDays, blacklisted })).toEqual({ score, grade });
  });

  it.each<CustomerD90Behavior | undefined>([
    undefined,
    {},
    { productViews: 0, validInquiries: 0, validRfqs: 0, loginDays: 0, spamInquiries: 0, blacklisted: 0 },
    { repliedInquiries: 100 },
    { productViews: -1, validInquiries: 2 },
    { productViews: NaN, validInquiries: 2 },
    { productViews: Infinity, validInquiries: 2 },
  ])("leaves unavailable or invalid metrics ungraded: %j", (behavior) => {
    expect(customerQualityOf(behavior)).toBeNull();
  });
});
