// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useAgentSessionWorkbench } from "@/features/agent/hooks/useAgentSessionWorkbench";
import { ApiError } from "@/services/httpAdapter";
import type { AgentConfig, AgentTestResult, AgentTestSession } from "@/types/agent";

const mocks = vi.hoisted(() => ({
  backend: { getAgentConsole: vi.fn(), listAgentTestHistory: vi.fn(), runAgentTest: vi.fn(), regenerateAgentTestSessionReply: vi.fn() },
  message: { error: vi.fn(), success: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => ({ App: { useApp: () => ({ message: mocks.message }) } }));
const agent: AgentConfig = { id: "agent", name: "Agent", category: "regular", enabled: true, capabilities: [], description: "", updatedAt: "now" };
const session: AgentTestSession = { id: "session-a", title: "Session", agentId: "agent", createdAt: "now", messages: [
  { id: "user", role: "user", content: "first", createdAt: "now" },
  { id: "assistant", role: "assistant", content: "reply", createdAt: "now" },
] };
let root: Root;
let container: HTMLDivElement;
let workbench: ReturnType<typeof useAgentSessionWorkbench>;
function Harness() {
  const current = useAgentSessionWorkbench();
  useEffect(() => { workbench = current; }, [current]);
  return <div>{current.operationError}</div>;
}
beforeEach(async () => {
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.backend.getAgentConsole.mockResolvedValue({ agents: [agent] });
  mocks.backend.listAgentTestHistory.mockResolvedValue([session, { ...session, id: "session-b" }]);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<Harness />));
  await act(async () => workbench.setDraft("pending input"));
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

it.each(["send", "regenerate"])("keeps %s uncertainty and reconciles only on manual history read", async (operation) => {
  const request = operation === "send" ? mocks.backend.runAgentTest : mocks.backend.regenerateAgentTestSessionReply;
  request.mockRejectedValue(new ApiError("timeout", "/api/agent/test", { kind: "timeout", requestId: "ai-42" }));
  await act(async () => { if (operation === "send") await workbench.sendMessage(); else await workbench.regenerateReply(); });
  expect(container.textContent).toContain("结果未知");
  expect(container.textContent).toContain("ai-42");
  expect(workbench.draft).toBe("pending input");
  expect(mocks.backend.listAgentTestHistory).toHaveBeenCalledTimes(1);
  const completed: AgentTestSession = { ...session, messages: [...session.messages, { id: "late-reply", role: "assistant", content: "completed in background", createdAt: "later" }] };
  mocks.backend.listAgentTestHistory.mockResolvedValueOnce([completed]);
  await act(async () => workbench.reconcileHistory());
  expect(workbench.activeSession?.messages.at(-1)?.content).toBe("completed in background");
  expect(workbench.draft).toBe("pending input");
  expect(request).toHaveBeenCalledTimes(1);
  expect(container.textContent).toContain("未出现结果不代表后台操作已停止");
});

it("does not clear a different session's draft when an earlier request completes", async () => {
  let resolve!: (result: AgentTestResult) => void;
  mocks.backend.runAgentTest.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  let pending!: Promise<void>;
  await act(async () => { pending = workbench.sendMessage(); });
  await act(async () => workbench.selectSession("session-b"));
  await act(async () => { resolve({ session }); await pending; });
  expect(workbench.activeSessionId).toBe("session-b");
  expect(workbench.draft).toBe("pending input");
});
