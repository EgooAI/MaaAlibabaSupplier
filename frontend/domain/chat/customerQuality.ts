import type { CustomerD90Behavior } from "@/types/chatCanonical";

export type CustomerQualityGrade = "S" | "A" | "B" | "C" | "D";

export interface CustomerQuality {
  score: number;
  grade: CustomerQualityGrade;
}

/** Calculates I′ from recorded behavior, keeping insufficient data separate from a zero score. */
export function customerQualityOf(behavior: CustomerD90Behavior | undefined): CustomerQuality | null {
  const values = [
    behavior?.productViews, behavior?.validInquiries, behavior?.validRfqs,
    behavior?.loginDays, behavior?.spamInquiries, behavior?.blacklisted,
  ].map((value) => value ?? 0);
  if (values.some((value) => !Number.isSafeInteger(value) || value < 0) || !values.some((value) => value > 0)) return null;

  const [a, b, c, d, e, f] = values;
  const raw = 2 * Math.log2(a + 2 * d + 4 * b + 8 * c + 1)
    * (1 + (4 * b + 8 * c + 1) / (a + 2 * d + 1))
    - 8 * Math.log2(e + f + 1) + 60;
  const score = Math.round(Math.min(100, Math.max(0, raw)));
  const grade = score >= 90 ? "S" : score >= 80 ? "A" : score >= 70 ? "B" : score >= 60 ? "C" : "D";
  return { score, grade };
}
