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
});
