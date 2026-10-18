// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LlmPage } from "@/features/agent/LlmPage";
import { AgentWorkbenchProvider } from "@/features/agent/hooks/useAgentWorkbench";
import type { AgentConsoleState, LlmLevelConfig } from "@/types/agent";

const mocks = vi.hoisted(() => ({
  message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
  getAgentConsole: vi.fn(),
  saveLlmConfig: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("@/services/client", () => ({
  backend: { getAgentConsole: mocks.getAgentConsole, saveLlmConfig: mocks.saveLlmConfig },
}));
vi.mock("@/features/account/AccountProvider", () => ({ useAccount: () => ({ refresh: mocks.refresh }) }));
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  return { ...actual, App: { useApp: () => ({ message: mocks.message }) } };
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

function level(level: number, model = ""): LlmLevelConfig {
  return {
    level,
    base_url: model ? `https://llm-${level}.example/v1` : "",
    api_key: model ? `key-${level}` : "",
    model_name: model,
    system_prompt: model ? `prompt-${level}` : "",
    context: 8000 + level * 1000,
    max_tool_rounds: 3,
  };
}

function consoleState(): AgentConsoleState {
  return { llmLevels: [level(0, "model-0"), level(1), level(2), level(3), level(4)], agents: [], history: [] };
}

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.getAgentConsole.mockImplementation(async () => consoleState());
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
      <AgentWorkbenchProvider>
        <LlmPage />
      </AgentWorkbenchProvider>,
    );
  });
  await act(async () => {});
}

function buttons(label: string) {
  return [...document.body.querySelectorAll("button")].filter((item) => item.textContent?.replace(/\s+/g, "") === label);
}

function button(label: string) {
  return buttons(label)[0];
}

function radioFor(level: number) {
  return [...document.body.querySelectorAll("label")]
    .find((item) => item.textContent?.startsWith(`Level ${level}`))
    ?.querySelector<HTMLInputElement>('input[type="radio"]');
}

describe("LlmPage copy as", () => {
  it("only offers copy for configured levels and excludes the source from the target list", async () => {
    await render();

    expect(buttons("复制为")).toHaveLength(1);
    expect(buttons("配置")).toHaveLength(4);

    await act(async () => button("复制为")?.click());

    expect(button("复制")?.hasAttribute("disabled")).toBe(true);
    expect(radioFor(0)).toBeUndefined();
    expect([...document.body.querySelectorAll('input[type="radio"]')]).toHaveLength(4);
  });

  it("overwrites the selected level with the source configuration", async () => {
    await render();
    await act(async () => button("复制为")?.click());
    mocks.saveLlmConfig.mockImplementation(async (input: LlmLevelConfig) => input);

    const target = radioFor(2);
    if (!target) throw new Error("target radio missing");
    await act(async () => target.click());
    await act(async () => button("复制")?.click());

    expect(mocks.saveLlmConfig).toHaveBeenCalledTimes(1);
    expect(mocks.saveLlmConfig).toHaveBeenCalledWith({
      level: 2,
      base_url: "https://llm-0.example/v1",
      api_key: "key-0",
      model_name: "model-0",
      system_prompt: "prompt-0",
      context: 8000,
      max_tool_rounds: 3,
    });
    expect(mocks.message.success).toHaveBeenCalledWith("已复制到 Level 2");
  });
});
