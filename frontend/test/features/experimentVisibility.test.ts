import { describe, expect, it } from "vitest";
import { isPlaygroundSession } from "@/features/agent/hooks/usePlaygroundWorkbench";
import type { AgentTestSession } from "@/types/agent";

function session(overrides: Partial<AgentTestSession> = {}): AgentTestSession {
  return {
    id: "s1",
    title: "会话",
    agentId: "agent-1",
    createdAt: "2026-10-01",
    messages: [],
    ...overrides,
  };
}

describe("playground session ownership", () => {
  it("keeps forks and standalone playground sessions in the playground list", () => {
    expect(isPlaygroundSession(session({ type: "fork" }))).toBe(true);
    expect(isPlaygroundSession(session({ type: "playground" }))).toBe(true);
  });

  it("hides experiment variants so the comparison owns their transcript", () => {
    // Continuing a variant here would append turns the experiment's step map
    // does not know about, so the board and the session would disagree.
    expect(isPlaygroundSession(session({ type: "playground", experimentId: "7" }))).toBe(false);
  });

  it("never treats regular test sessions as playground sessions", () => {
    expect(isPlaygroundSession(session({ type: "test" }))).toBe(false);
  });
});
