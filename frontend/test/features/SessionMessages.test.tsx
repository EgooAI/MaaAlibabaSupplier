// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SessionMessages } from "@/features/agent/components/SessionMessages";
import type { AgentTestSession } from "@/types/agent";

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

const session: AgentTestSession = {
  id: "session-1",
  agentId: "agent-1",
  title: "试运行",
  createdAt: "2026-09-18T10:00:00+08:00",
  messages: [
    { id: "m1", role: "user", content: "帮我写两条", createdAt: "2026-09-18T10:00:00+08:00" },
    { id: "m2", role: "assistant", content: "- 第一条\n- 第二条", createdAt: "2026-09-18T10:00:01+08:00" },
  ],
};

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("SessionMessages", () => {
  it("renders agent replies as markdown", async () => {
    await act(async () => {
      root.render(<SessionMessages session={session} />);
    });

    expect([...container.querySelectorAll("li")].map((item) => item.textContent)).toEqual(["第一条", "第二条"]);
  });

  it("marks a draft session as an unsaved snapshot", async () => {
    await act(async () => {
      root.render(<SessionMessages session={{ ...session, draft: true }} />);
    });

    expect(container.textContent).toContain("未保存的提示词快照");
  });

  it("renders tool cards in execution order before the final text", async () => {
    const withTools: AgentTestSession = {
      ...session,
      messages: [
        session.messages[0],
        {
          ...session.messages[1],
          content: "理由",
          iterations: 3,
          durationMs: 1200,
          toolCalls: [
            { callId: "c1", name: "first_lookup", arguments: { q: "a" }, ok: true, content: { a: 1 }, durationMs: 4, round: 1 },
            { callId: "c2", name: "second_lookup", arguments: { q: "b" }, ok: true, content: { b: 2 }, durationMs: 6, round: 2 },
          ],
        },
      ],
    };
    await act(async () => {
      root.render(<SessionMessages session={withTools} />);
    });

    const cards = [...container.querySelectorAll("button[aria-expanded]")];
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toContain("first_lookup");
    expect(cards[1].textContent).toContain("second_lookup");
    expect(container.textContent).toContain("LLM 3 轮");
    expect(container.textContent).toContain("理由");
  });

  it("auto-expands a failed call and keeps its reason visible", async () => {
    const failed: AgentTestSession = {
      ...session,
      messages: [{
        ...session.messages[1],
        content: "",
        toolCalls: [{ callId: "c1", name: "crm_lookup", arguments: { id: 1 }, ok: false, error: "工具执行超时。" }],
      }],
    };
    await act(async () => {
      root.render(<SessionMessages session={failed} />);
    });

    const card = container.querySelector("button[aria-expanded]");
    expect(card?.getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("工具执行超时。");
  });

  it("renders a reply intent as a simulated customer bubble", async () => {
    const simulated: AgentTestSession = {
      ...session,
      type: "fork",
      source: { conversationTitle: "Sofia", messageIndex: 2 },
      messages: [{
        id: "m9", role: "assistant", content: "", createdAt: "", mode: "simulated",
        toolCalls: [{
          callId: "c1", name: "reply_customer", ok: true,
          arguments: { reply: "We can ship tomorrow.", zh: "明天可以发货。", tone: "friendly" },
        }],
      }],
    };
    await act(async () => {
      root.render(<SessionMessages session={simulated} />);
    });

    const card = container.querySelector("button[aria-expanded]");
    act(() => card?.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(container.textContent).toContain("We can ship tomorrow.");
    expect(container.textContent).toContain("中文参考");
    expect(container.textContent).toContain("模拟");
  });

  it("shows the fork notice and only cards when the final text is empty", async () => {
    const cardsOnly: AgentTestSession = {
      ...session,
      type: "fork",
      source: { conversationTitle: "Sofia", messageIndex: 1 },
      messages: [{
        id: "m3", role: "assistant", content: "", createdAt: "",
        toolCalls: [{ callId: "c1", name: "reply_customer", arguments: { reply: "Hi" }, ok: true, content: {} }],
      }],
    };
    await act(async () => {
      root.render(<SessionMessages session={cardsOnly} />);
    });

    expect(container.textContent).toContain("模拟会话");
    expect(container.textContent).toContain("reply_customer");
    expect(container.querySelectorAll("button[aria-expanded]")).toHaveLength(1);
  });
});
