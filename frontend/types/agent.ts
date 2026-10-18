import type { ID } from "@/types/common";

export interface DocumentLlmConfig {
  level: number;
  base_url: string;
  api_key: string;
  model_name: string;
  system_prompt: string;
  context: number;
  max_tool_rounds: number | null;
}

export interface DbAgentPreset {
  apid: string;
  name: string;
  description: string;
  prompt: string;
  intelevel: number;
  tools: string[];
  enabled: boolean;
  updated_at: string;
  category?: "system" | "regular";
  revision_source?: string;
  revision_note?: string;
}

export interface AgentPresetRevision {
  id: number;
  apid: string;
  name: string;
  description: string;
  prompt: string;
  intelevel: number;
  tools: string[];
  source: string;
  note: string;
  created_at: string;
}

export interface AgentPreset {
  id: ID;
  name: string;
  description: string;
  prompt: string;
  level: number;
  tools?: string[];
  category: "system" | "regular";
  enabled: boolean;
  updated_at: string;
}

export interface AgentEditValues {
  name?: string;
  description?: string;
  prompt?: string;
  level?: number;
  capabilities?: string[];
}

export interface AgentSaveOptions {
  revisionNote?: string;
}

export interface SystemAgentDefinition {
  display_name: string;
  apid: string;
  description: string;
  prompt: string;
  intelevel: number;
  tools: string[];
}

/** A tool the backend actually registered; only these can be attached to an Agent. */
export interface AgentToolDefinition {
  name: string;
  description: string;
}

export interface LlmLevelConfig {
  level: number;
  base_url: string;
  api_key: string;
  model_name: string;
  system_prompt: string;
  context: number;
  max_tool_rounds: number | null;
}

export interface AgentConfig {
  id: ID;
  name: string;
  category: "system" | "regular";
  enabled: boolean;
  capabilities: string[];
  description: string;
  updatedAt: string;
  prompt?: string;
  level?: number;
  apid?: string;
}

export interface AgentTestMessage {
  id: ID;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
}

export interface AgentTestSession {
  id: ID;
  title: string;
  agentId: ID;
  draft?: boolean;
  createdAt: string;
  messages: AgentTestMessage[];
}

export interface AgentTestDraftInput {
  prompt: string;
  level: number;
  tools: string[];
}

export interface AgentTestInput {
  agentId: ID;
  content?: string;
  sessionId?: ID;
  draft?: AgentTestDraftInput;
}

export interface AgentTestResult {
  session: AgentTestSession;
  reply?: AgentTestMessage;
}

export interface AgentConsoleState {
  llmLevels: LlmLevelConfig[];
  agents: AgentConfig[];
  agentPresets?: AgentPreset[];
  systemAgents?: SystemAgentDefinition[];
  tools?: AgentToolDefinition[];
  history: AgentTestSession[];
}
