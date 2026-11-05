// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TimeText } from "@/components/TimeText";

let root: Root;
let container: HTMLDivElement;

const render = async (value: string | number) => {
  await act(async () => root.render(<TimeText value={value} />));
};

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.useFakeTimers();
  vi.setSystemTime(new Date(2026, 10, 5, 12, 0, 0));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("TimeText", () => {
  it("renders the smart label and exposes the exact time on hover", async () => {
    await render("2026-11-05 08:00");
    const time = container.querySelector("time")!;
    expect(time.textContent).toBe("今天 08:00");
    expect(time.getAttribute("title")).toBe("2026-11-05 08:00:00");
  });

  it("re-renders when the relative label expires", async () => {
    await render("2026-11-05 11:59:30");
    expect(container.textContent).toBe("刚刚");

    await act(async () => vi.advanceTimersByTime(30_000));
    expect(container.textContent).toBe("1分钟前");
  });

  it("keeps unknown values readable instead of inventing a timestamp", async () => {
    await render("未知时间");
    const time = container.querySelector("time")!;
    expect(time.textContent).toBe("未知时间");
    expect(time.hasAttribute("title")).toBe(false);
  });
});
