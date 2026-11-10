// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CountryFlag } from "@/components/CountryFlag";

let root: Root;
let container: HTMLDivElement;

const render = async (element: ReactNode) => {
  await act(async () => root.render(element));
};

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("CountryFlag", () => {
  it("renders the full country name with the code in parentheses", async () => {
    await render(<CountryFlag country="ID" showName />);

    const marker = container.querySelector<HTMLElement>('[data-country-code="ID"]')!;
    expect(marker.textContent).toBe("印度尼西亚 (ID)");
    expect(marker.getAttribute("aria-label")).toBe("印度尼西亚 ID");
  });
});
