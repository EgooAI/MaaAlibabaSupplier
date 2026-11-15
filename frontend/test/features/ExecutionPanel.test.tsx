// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ExecutionPanel } from "@/features/status/ExecutionPanel";
import type { ExecutionRunDetail, ExecutionState } from "@/types/execution";

const mocks = vi.hoisted(() => ({
  backend: {
    getExecutionState: vi.fn(),
    setExecutionEnabled: vi.fn(),
    submitExecutionRun: vi.fn(),
    getExecutionRun: vi.fn(),
    cancelExecutionRun: vi.fn(),
    listExecutionRuns: vi.fn(),
  },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));

const limits = {
  max_script_bytes: 65536, max_output_chars: 1048576, min_timeout_s: 1,
  default_timeout_s: 60, max_timeout_s: 300, max_runs: 20, max_age_s: 86400,
};
const disabledState: ExecutionState = {
  enabled: false, generation: 1, start_token: "a".repeat(32),
  interpreter: "C:\\app\\backend\\python\\python.exe", interpreter_available: true,
  cwd: "C:\\app", active_run_id: null, limits, runs: [],
};
const enabledState: ExecutionState = { ...disabledState, enabled: true, generation: 2 };
const detail: ExecutionRunDetail = {
  id: "b".repeat(32), status: "succeeded", created_at: 1_800_000_000, started_at: 1_800_000_000,
  finished_at: 1_800_000_001, timeout_s: 60, exit_code: 0, cancel_requested: false,
  script_bytes: 24, error: null, stdout_chars: 8, stderr_chars: 0,
  stdout_truncated: false, stderr_truncated: false, script: "print('hello')",
  stdout: "hello\n", stderr: "", stdout_offset: 6, stderr_offset: 0, stdout_total: 6, stderr_total: 0,
};

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  mocks.backend.getExecutionState.mockResolvedValue(structuredClone(disabledState));
  mocks.backend.setExecutionEnabled.mockResolvedValue(structuredClone(enabledState));
  mocks.backend.submitExecutionRun.mockResolvedValue(structuredClone(detail));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

async function render() {
  await act(async () => root.render(<ExecutionPanel />));
  await act(async () => {});
}

function button(text: string): HTMLButtonElement {
  const node = [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent === text);
  expect(node, `Missing button: ${text}`).toBeTruthy();
  return node!;
}

it("keeps execution blocked until the switch is enabled and sends the current switch version", async () => {
  await render();
  expect(container.textContent).toContain("远程执行已关闭");
  expect(button("运行").disabled).toBe(true);

  const toggle = container.querySelector<HTMLButtonElement>("[role=switch]")!;
  expect(toggle.getAttribute("aria-checked")).toBe("false");
  await act(async () => toggle.click());
  expect(mocks.backend.setExecutionEnabled).toHaveBeenCalledWith(true, 1);
  expect(container.textContent).toContain("远程执行已开启");
  expect(button("运行").disabled).toBe(false);
});

it("submits the edited script and renders the returned output", async () => {
  mocks.backend.getExecutionState.mockResolvedValue(structuredClone(enabledState));
  await render();
  const editor = container.querySelector<HTMLTextAreaElement>("textarea")!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    setter.call(editor, "print('hello')");
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => button("运行").click());
  expect(mocks.backend.submitExecutionRun).toHaveBeenCalledWith(expect.objectContaining({
    start_token: enabledState.start_token, generation: 2, script: "print('hello')", timeout_s: 60,
  }));
  expect(container.textContent).toContain("执行结果");
  expect(container.textContent).toContain("已成功");
  expect(container.textContent).toContain("hello");
});
