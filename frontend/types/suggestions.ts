import type { AgentSessionPin, AgentToolCallRecord, DbAgentPreset } from "./agent";
import type { SuggestionReply } from "./chatCanonical";

export type SuggestionDraft = {
  prompt: string;
  level: number;
  tools: string[];
  requires_tool: boolean;
};

export type SuggestionRunInput = {
  runId: string;
  agentId: string;
  draft: SuggestionDraft | null;
  contextRunId: string | null;
};

export type SuggestionRun = {
  id: string;
  conversationId: string;
  agentId: string;
  agentName: string;
  status: "queued" | "running" | "completed" | "failed";
  legacy: boolean;
  createdAt: number;
  error: string | null;
  request: SuggestionRunInput;
  pinned: AgentSessionPin;
  context: { id: string; messageCount: number; capturedAt: number; lastMessageId: string; lastMessageAt: string };
  result: {
    reply: SuggestionReply;
    reasoning: string;
    toolCalls: AgentToolCallRecord[];
  } | null;
};

export type SuggestionCatalog = { defaultAgentId: string; agents: DbAgentPreset[]; template: DbAgentPreset };
