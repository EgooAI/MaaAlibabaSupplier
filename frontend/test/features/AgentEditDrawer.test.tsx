// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { AgentEditDrawer } from "@/features/agent/AgentEditDrawer";
import type { AgentConfig } from "@/types/agent";

const mocks = vi.hoisted(() => ({
  message: { warning: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn() },
  modal: { confirm: vi.fn() },
}));

vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return { ...actual, App: { useApp: () => ({ message: mocks.message, modal: mocks.modal }) } };
});

if (!globalThis.ResizeObserver) {
  Object.assign(globalThis, {
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}
if (!window.matchMedia) {
  Object.assign(window, {
    matchMedia: () => ({
      matches: false, media: "", onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  });
}

const agent: AgentConfig = {
  id: "agent-1", name: "报价助手", category: "regular", enabled: true,
  capabilities: [], description: "d", updatedAt: "", prompt: "旧的提示词", level: 0, apid: "agent-1",
};

let root: Root;
let container: HTMLDivElement;
let onClose: Mock<() => void>;

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  onClose = vi.fn<() => void>();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render() {
  await act(async () => {
    root.render(
      <AgentEditDrawer
        agent={agent}
        category="regular"
        systemAgents={[]}
        llmLevels={[]}
        open
        saving={false}
        onClose={onClose}
        onSave={vi.fn()}
      />,
    );
  });
}

function button(label: string) {
  return [...document.body.querySelectorAll("button")].find((item) => item.textContent?.replace(/\s+/g, "") === label);
}

async function renameAgent(value: string) {
  const input = document.body.querySelector<HTMLInputElement>('input[placeholder="例如：报价跟进助手"]');
  if (!input) throw new Error("name input missing");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("AgentEditDrawer unsaved guard", () => {
  it("closes directly when nothing changed", async () => {
    await render();
    await act(async () => button("取消")?.click());

    expect(mocks.modal.confirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("confirms before discarding edits and keeps editing when cancelled", async () => {
    await render();
    await renameAgent("改过的名字");
    await act(async () => button("取消")?.click());

    expect(onClose).not.toHaveBeenCalled();
    expect(mocks.modal.confirm).toHaveBeenCalledTimes(1);

    const config = mocks.modal.confirm.mock.calls[0][0] as { onOk: () => void };
    await act(async () => config.onOk());

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
