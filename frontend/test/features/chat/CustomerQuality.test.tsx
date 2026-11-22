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

it("shows six metrics in two rows of three without formula notation and distinguishes missing values from zero", () => {
  const container = document.createElement("div");
  container.innerHTML = renderToStaticMarkup(<CustomerQualityDetails behavior={{ validInquiries: 0, validRfqs: 0, loginDays: 1, spamInquiries: 0, blacklisted: 1, repliedInquiries: 12 }} />);
  expect(container.textContent).toContain("56 / 100");
  const rows = [...container.querySelectorAll("table tbody tr")]
    .map((row) => [...row.querySelectorAll("td")].map((cell) => cell.textContent));
  expect(rows).toEqual([
    ["浏览商品—", "有效询盘0", "有效 RFQ0"],
    ["登录天数1", "垃圾询盘0", "被拉黑次数1"],
  ]);
  expect(container.textContent).toContain("已回复询盘：12");
  expect(container.querySelector("details")).toBeNull();
  expect(container.textContent).not.toMatch(/I′|log₂|计算与评级说明|缺失项按 0/);

  container.innerHTML = renderToStaticMarkup(<CustomerQualityDetails behavior={{ loginDays: 0 }} />);
  expect(container.querySelector(".ant-tag")?.textContent).toBe("数据不足");
  expect(container.textContent).not.toContain("/ 100");
});
