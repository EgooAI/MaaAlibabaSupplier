// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComparisonBoard } from "@/features/agent/experiment/components/ComparisonBoard";
import { locateStepTurns } from "@/features/agent/experiment/stepTurns";
import type { AgentTestMessage, AgentTestSession } from "@/types/agent";
import type { Experiment, ExperimentVariant } from "@/types/experiment";

if (!globalThis.ResizeObserver) {
  Object.assign(globalThis, {
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}

// The board picks one-variant tabs below lg. Reporting every min-width query as
// matching keeps the wide grid, which is what a desktop user sees.
Object.assign(window, {
  matchMedia: (query: string) => ({
    matches: query.includes("min-width"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

function message(id: string, role: AgentTestMessage["role"], content: string): AgentTestMessage {
  return { id, role, content, createdAt: "2026-10-29T10:00:00+08:00" };
}

const SESSION_ONE = "session-1";
const SESSION_TWO = "session-2";

/** Step 0 is a buyer message, step 1 an agent turn: both variants finished both. */
function buildExperiment(): Experiment {
  const first: AgentTestSession = {
    id: SESSION_ONE,
    agentId: "agent-quote",
    title: "报价助手 · 当前版本",
    createdAt: "2026-10-29T10:00:00+08:00",
    messages: [
      message("a1", "user", "Can you ship within 7 days?"),
      message("a2", "assistant", "第一版：7 天内可以发货。"),
      message("a3", "assistant", "第一版：那就按 7 天安排。"),
    ],
    pinned: { kind: "preset", revision_id: 12, llm_level: 2, tools: ["query_product", "calc"] },
  };
  const second: AgentTestSession = {
    id: SESSION_TWO,
    agentId: "agent-quote",
    title: "报价助手 · 严格账期",
    createdAt: "2026-10-29T10:00:00+08:00",
    messages: [
      message("b1", "user", "Can you ship within 7 days?"),
      message("b2", "assistant", "第二版：需要 10 天。"),
      message("b3", "assistant", "第二版：账期不变。"),
    ],
    pinned: { kind: "draft", llm_level: 3, tools: [] },
  };
  const variants: ExperimentVariant[] = [
    { ...first, label: "当前版本" },
    { ...second, label: "严格账期" },
  ];
  return {
    id: "1",
    agentId: "agent-quote",
    name: "发货时效对比",
    frozenPrefix: "客户要求先发货、收货后 60 天付款。",
    source: { kind: "conversation", conversationId: 7, messageIndex: 2, conversationTitle: "Sofia", frozenChars: 20 },
    steps: [
      { index: 0, mode: "buyer", text: "Can you ship within 7 days?", label: "" },
      { index: 1, mode: "agent", text: "", label: "" },
    ],
    // The first variant is marked better on step 0: the cell gets a green edge.
    annotations: [{ step: 0, variantId: SESSION_ONE, verdict: "better", note: "" }],
    notes: "",
    progress: {
      "0": { variants: [SESSION_ONE, SESSION_TWO], before: { [SESSION_ONE]: 0, [SESSION_TWO]: 0 } },
      "1": { variants: [SESSION_ONE, SESSION_TWO], before: { [SESSION_ONE]: 2, [SESSION_TWO]: 2 } },
    },
    createdAt: "2026-10-29T10:00:00+08:00",
    updatedAt: "2026-10-29T10:00:00+08:00",
    activeRunId: null,
    variants,
  };
}

describe("locateStepTurns", () => {
  it("reads the buyer turn and the reply it caused", () => {
    const experiment = buildExperiment();
    const turns = locateStepTurns(experiment.variants[0], 0, experiment);

    expect(turns?.user?.content).toBe("Can you ship within 7 days?");
    expect(turns?.assistant?.content).toBe("第一版：7 天内可以发货。");
  });

  it("reads only the reply for an agent step", () => {
    const experiment = buildExperiment();
    const turns = locateStepTurns(experiment.variants[1], 1, experiment);

    // The step's own turn, not the buyer turn's reply that precedes it.
    expect(turns?.user).toBeUndefined();
    expect(turns?.assistant?.content).toBe("第二版：账期不变。");
  });

  it("keeps two steps on their own turns", () => {
    const experiment = buildExperiment();
    const [first, second] = [0, 1].map((stepIndex) => locateStepTurns(experiment.variants[0], stepIndex, experiment));

    expect(first?.assistant?.id).toBe("a2");
    expect(second?.assistant?.id).toBe("a3");
  });

  it("returns nothing for a variant that has not finished the step", () => {
    const experiment = buildExperiment();
    const unfinished: Experiment = {
      ...experiment,
      // The second variant never ran step 1: it must not borrow step 0's reply.
      progress: { ...experiment.progress, "1": { variants: [SESSION_ONE], before: { [SESSION_ONE]: 2 } } },
    };

    expect(locateStepTurns(unfinished.variants[1], 1, unfinished)).toBeNull();
    expect(locateStepTurns(unfinished.variants[0], 1, unfinished)?.assistant?.id).toBe("a3");
  });

  it("falls back to the step's ordinal position when no checkpoint was recorded", () => {
    const experiment = buildExperiment();
    const legacy: Experiment = {
      ...experiment,
      // Older records know a variant finished the step but not where it started.
      progress: {
        "0": { variants: [SESSION_ONE, SESSION_TWO] },
        "1": { variants: [SESSION_ONE, SESSION_TWO] },
      },
    };

    // One buyer step sits before step 1, so step 1 starts after two messages.
    expect(locateStepTurns(legacy.variants[0], 1, legacy)?.assistant?.id).toBe("a3");
  });
});

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ComparisonBoard", () => {
  const render = async (experiment: Experiment, onAnnotate = vi.fn()) => {
    await act(async () => {
      root.render(
        <ComparisonBoard
          experiment={experiment}
          onAnnotate={onAnnotate}
          onPromote={vi.fn()}
          onRemoveVariant={vi.fn()}
          onRetryStep={vi.fn()}
        />,
      );
    });
    return onAnnotate;
  };

  it("shows every variant's own answer side by side", async () => {
    await render(buildExperiment());

    expect(container.textContent).toContain("第一版：7 天内可以发货。");
    expect(container.textContent).toContain("第二版：需要 10 天。");
    // Each column states the version it pinned.
    expect(container.textContent).toContain("已保存版本");
    expect(container.textContent).toContain("12");
    expect(container.textContent).toContain("草稿快照");
  });

  it("marks the variant a merchant judged better", async () => {
    await render(buildExperiment());

    const better = container.querySelector(".border-l-green-500");
    expect(better).not.toBeNull();
    expect(better?.textContent).toContain("第一版：7 天内可以发货。");
    expect(better?.textContent).not.toContain("第二版：需要 10 天。");
  });

  it("records a note against one cell without touching the others", async () => {
    const experiment = buildExperiment();
    const onAnnotate = await render(experiment);
    const note = container.querySelector<HTMLInputElement>(
      'input[aria-label="第 1 步 严格账期 备注"]',
    );
    expect(note).not.toBeNull();

    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      setter?.call(note, "答得太强硬");
      note!.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      note!.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    });

    expect(onAnnotate).toHaveBeenCalledTimes(1);
    expect(onAnnotate.mock.calls[0][0]).toEqual([
      { step: 0, variantId: SESSION_ONE, verdict: "better", note: "" },
      { step: 0, variantId: SESSION_TWO, verdict: "", note: "答得太强硬" },
    ]);
  });
});
