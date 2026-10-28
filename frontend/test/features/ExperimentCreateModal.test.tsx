// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { ExperimentCreateModal } from "@/features/agent/experiment/ExperimentCreateModal";
import type { CreateExperimentSourceInput, Experiment } from "@/types/experiment";

const mocks = vi.hoisted(() => ({
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  agents: [{ id: "agent-1", name: "报价助手", enabled: true }],
  revisions: [
    { id: 11, apid: "agent-1", name: "报价助手", description: "", prompt: "旧提示词", intelevel: 0, tools: [], source: "manual", note: "", created_at: "2026-10-01 10:00" },
  ],
  getAgentConsole: vi.fn(),
  listAgentPresetRevisions: vi.fn(),
  createExperiment: vi.fn(),
}));

vi.mock("@/services/client", () => ({
  backend: {
    getAgentConsole: mocks.getAgentConsole,
    listAgentPresetRevisions: mocks.listAgentPresetRevisions,
    createExperiment: mocks.createExperiment,
  },
}));
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

const created: Experiment = {
  id: "7", agentId: "agent-1", name: "报价助手 对比实验", frozenPrefix: "", source: { kind: "text" },
  steps: [], annotations: [], notes: "", progress: {}, createdAt: "", updatedAt: "", activeRunId: null, variants: [],
};

let root: Root;
let container: HTMLDivElement;
let onClose: Mock<() => void>;
let onCreated: Mock<(experiment: Experiment) => void>;

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.getAgentConsole.mockImplementation(async () => ({ llmLevels: [], agents: mocks.agents, history: [] }));
  mocks.listAgentPresetRevisions.mockImplementation(async () => mocks.revisions);
  mocks.createExperiment.mockImplementation(async () => created);
  onClose = vi.fn<() => void>();
  onCreated = vi.fn<(experiment: Experiment) => void>();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(props: {
  source?: CreateExperimentSourceInput;
  initialDraft?: { prompt: string; level: number; tools: string[]; requiresTool?: boolean };
  initialRevisionId?: number;
} = {}) {
  await act(async () => {
    root.render(
      <ExperimentCreateModal
        open
        initialAgentId="agent-1"
        onClose={onClose}
        onCreated={onCreated}
        {...props}
      />,
    );
  });
  // Agent list and revisions resolve in effects.
  await act(async () => { await Promise.resolve(); });
}

function button(label: string) {
  return [...document.body.querySelectorAll("button")].find((item) => item.textContent?.replace(/\s+/g, "") === label);
}

function rows() {
  return [...document.body.querySelectorAll<HTMLElement>('[data-testid="variant-row"]')];
}

function kindSelect(index: number) {
  const select = rows()[index]?.querySelector<HTMLInputElement>(".ant-select input[role='combobox']");
  if (!select) throw new Error(`variant ${index} kind select missing`);
  return select;
}

/** Opens an antd Select and clicks the option carrying this label. */
async function choose(select: HTMLInputElement, label: string) {
  await act(async () => {
    select.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    select.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  const option = [...document.body.querySelectorAll<HTMLElement>(".ant-select-item-option")]
    .find((item) => item.textContent?.trim() === label);
  if (!option) throw new Error(`option ${label} missing`);
  await act(async () => {
    option.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

function fillTextarea(value: string) {
  const textarea = document.body.querySelector<HTMLTextAreaElement>("textarea");
  if (!textarea) throw new Error("text textarea missing");
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set;
  setter?.call(textarea, value);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
}

function fillRowLabel(index: number, value: string) {
  const input = rows()[index]?.querySelector<HTMLInputElement>("input[placeholder]");
  if (!input) throw new Error(`variant ${index} label input missing`);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function submit() {
  await act(async () => button("创建实验并开始对比")?.click());
}

describe("ExperimentCreateModal", () => {
  it("starts with two variants and disables deleting the last one", async () => {
    await render();

    expect(rows()).toHaveLength(2);
    expect(rows().map((row) => row.textContent)).toEqual([expect.stringContaining("当前保存配置"), expect.stringContaining("当前保存配置")]);
    expect(document.body.textContent).toContain("基线");
    // 「编辑器草稿」needs the editor buffer: without it the option is not offered.
    expect(document.body.textContent).not.toContain("编辑器草稿");
  });

  it("sends the editor draft as a variant wherever the user pins it", async () => {
    const draft = { prompt: "坚持 30% 预付", level: 2, tools: ["calculate"], requiresTool: true };
    await render({ initialDraft: draft });

    // Row 2 starts on the draft; moving the draft to row 1 must release it from row 2.
    await choose(kindSelect(0), "编辑器草稿");
    expect(rows()[1].textContent).toContain("当前保存配置");
    await submit();

    expect(mocks.createExperiment).toHaveBeenCalledTimes(1);
    const input = mocks.createExperiment.mock.calls[0][0] as { agentId: string; variants: unknown[] };
    expect(input.agentId).toBe("agent-1");
    expect(input.variants[0]).toEqual({ label: undefined, versionKind: "draft", draft });
    expect(input.variants[1]).toEqual({ label: undefined, versionKind: "preset" });
    expect(onCreated).toHaveBeenCalledWith(created);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("submits the edited text as the starting point", async () => {
    await render({ source: { kind: "text", text: "" } });

    fillTextarea("客户要求先发货、收货后 60 天付款。");
    fillRowLabel(1, "严格账期");
    await submit();

    expect(mocks.listAgentPresetRevisions).not.toHaveBeenCalled();
    const input = mocks.createExperiment.mock.calls[0][0] as { source: CreateExperimentSourceInput; variants: unknown[] };
    expect(input.source).toEqual({ kind: "text", text: "客户要求先发货、收货后 60 天付款。" });
    expect(input.variants[1]).toMatchObject({ label: "严格账期" });
  });

  it("pins a historical revision for the second variant", async () => {
    await render({ source: { kind: "text", text: "MOQ?" }, initialRevisionId: 11 });

    // Revisions resolve through a promise chain: wait for the select to show one.
    for (let attempt = 0; attempt < 20 && !rows()[1].textContent?.includes("旧提示词"); attempt += 1) {
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    }
    expect(mocks.listAgentPresetRevisions).toHaveBeenCalledWith("agent-1");
    expect(rows()[1].textContent).toContain("旧提示词");

    await submit();

    const input = mocks.createExperiment.mock.calls[0][0] as { variants: unknown[] };
    expect(input.variants[1]).toEqual({ label: undefined, versionKind: "preset", revisionId: 11 });
  });
});
