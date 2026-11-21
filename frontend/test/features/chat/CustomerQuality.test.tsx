// @vitest-environment happy-dom
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { CustomerQualityBadge, CustomerQualityDetails } from "@/features/chat/customer/CustomerQuality";

it.each([
  [{ validRfqs: 1 }, "S · 100", "#b91c1c"],
  [{ validRfqs: 1, loginDays: 1, blacklisted: 1 }, "A · 80", "#a15c00"],
  [{ validRfqs: 1, loginDays: 3, blacklisted: 1 }, "B · 70", "#6d28d9"],
  [{ loginDays: 5, blacklisted: 1 }, "C · 60", "#1d4ed8"],
  [{ spamInquiries: 255 }, "D · 0", "#52675b"],
] as const)("renders the specified grade color and score for %j", (behavior, text, color) => {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(<CustomerQualityBadge behavior={behavior} />);
  const badge = container.querySelector<HTMLElement>(".ant-tag")!;
  expect(badge.textContent).toBe(text);
  expect(badge.style.color).toBe(color);
});

it("explains missing inputs alongside the score and retains the non-scoring inquiry metric", () => {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(<CustomerQualityDetails behavior={{ loginDays: 1, blacklisted: 1, repliedInquiries: 12 }} />);
  expect(container.textContent).toContain("56 / 100");
  const fields = [...container.querySelectorAll(".ant-descriptions-item")].map((item) => item.textContent);
  expect(fields).toContain("A · 浏览—");
  expect(fields).toContain("D · 登录天数1");
  expect(fields).toContain("F · 拉黑1");
  expect(fields).toContain("已回复询盘12");
  expect(container.querySelector("details")?.open).toBe(false);
  expect(container.querySelector("details")?.textContent).toContain("已回复询盘不参与计分");

  container.innerHTML = renderToStaticMarkup(<CustomerQualityDetails behavior={{ loginDays: 0 }} />);
  expect(container.querySelector(".ant-tag")?.textContent).toBe("数据不足");
  expect(container.textContent).not.toContain("/ 100");
});
