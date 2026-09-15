import type { AgentConfig, AgentTestMessage, AgentTestSession } from "@/types/agent";

export type AgentTestTurn = {
  user: AgentTestMessage;
  assistant: AgentTestMessage;
  startIndex: number;
  endIndex: number;
};

export function getLatestAgentTestTurn(session: AgentTestSession): AgentTestTurn | null {
  const endIndex = session.messages.length - 1;
  const assistant = session.messages[endIndex];
  const user = session.messages[endIndex - 1];
  if (!assistant || !user || user.role !== "user" || assistant.role !== "assistant") return null;
  return { user, assistant, startIndex: endIndex - 1, endIndex };
}

export function canUndoAgentTestTurn(session: AgentTestSession) {
  return getLatestAgentTestTurn(session) !== null;
}

export function canRegenerateAgentTestReply(session: AgentTestSession) {
  return getLatestAgentTestTurn(session) !== null;
}

export function removeLatestAgentTestTurn(session: AgentTestSession) {
  const turn = getLatestAgentTestTurn(session);
  if (!turn) return null;
  return { ...session, messages: session.messages.slice(0, turn.startIndex) };
}

export function replaceLatestAgentTestReply(session: AgentTestSession, reply: AgentTestMessage) {
  const turn = getLatestAgentTestTurn(session);
  if (!turn) return null;
  return { ...session, messages: session.messages.map((message, index) => index === turn.endIndex ? reply : message) };
}

export function nextAgentTestSessionId(sessions: AgentTestSession[], removedId: string) {
  const index = sessions.findIndex((session) => session.id === removedId);
  const next = sessions.filter((session) => session.id !== removedId);
  return next[Math.max(0, index - 1)]?.id ?? next[0]?.id;
}

export function agentCategoryLabel(category: AgentConfig["category"]) {
  return category === "system" ? "系统 Agent" : "普通 Agent";
}

export function filterAgentsByCategory(agents: AgentConfig[], category: AgentConfig["category"]) {
  return agents.filter((agent) => agent.category === category);
}

export function filterAgentTestSessionsByCategory(sessions: AgentTestSession[], agents: AgentConfig[], category: AgentConfig["category"]) {
  const agentIds = new Set(filterAgentsByCategory(agents, category).map((agent) => agent.id));
  return sessions.filter((session) => agentIds.has(session.agentId));
}

export function canRunAgentExecution(agent: AgentConfig | undefined): agent is AgentConfig {
  return Boolean(agent?.enabled);
}
