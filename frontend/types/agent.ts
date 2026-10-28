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
  requires_tool?: boolean;
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
  requires_tool?: boolean;
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
  requiresTool?: boolean;
  updated_at: string;
}

export interface AgentEditValues {
  name?: string;
  description?: string;
  prompt?: string;
  level?: number;
  capabilities?: string[];
  requiresTool?: boolean;
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
  requires_tool?: boolean;
}

export type AgentToolCategory = "read" | "effect";

/** A tool the backend actually registered; only these can be attached to an Agent. */
export interface AgentToolDefinition {
  name: string;
  /** 中文名；未设置时等于 name。 */
  title?: string;
  category?: AgentToolCategory;
  description: string;
  /** 参数名 → 参数说明。 */
  parameters?: Record<string, string>;
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
  /** The Agent must call a tool: the pipeline forces a tool call. */
  requiresTool?: boolean;
}

export interface AgentToolCallRecord {
  callId?: string | null;
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  content?: unknown;
  error?: string | null;
  durationMs?: number;
  round?: number;
  /** Set when a read result was cut to the executor's size limit. */
  originalChars?: number | null;
}

export interface AgentTurnIntent {
  intentId?: string | null;
  kind: string;
  status: "requested" | "accepted" | "discarded" | "simulated" | string;
  callId?: string | null;
  batchId?: string;
  sequence?: number;
  reply: string;
  zh: string;
  tone: string;
  conversationId?: number | null;
}

export interface AgentTurnUsage {
  promptTokens?: number | null;
  completionTokens?: number | null;
  totalTokens?: number | null;
}

export interface AgentTestMessage {
  id: ID;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  toolCalls?: AgentToolCallRecord[];
  intents?: AgentTurnIntent[];
  usage?: AgentTurnUsage;
  iterations?: number;
  durationMs?: number;
  restricted?: boolean;
  /** live / simulated: simulated turns never queue a send. */
  mode?: "live" | "simulated";
}

export interface AgentSessionSource {
  sessionId?: string | null;
  messageIndex?: number | null;
  conversationId?: number | null;
  conversationTitle?: string;
  createdAt?: number;
  frozenChars?: number;
}

export interface AgentSessionPin {
  kind: "preset" | "draft";
  revision_id?: number | null;
  name?: string;
  prompt?: string;
  llm_level?: number;
  tools?: string[];
  requires_tool?: boolean;
}

export interface AgentTestSession {
  id: ID;
  title: string;
  agentId: ID;
  draft?: boolean;
  createdAt: string;
  /** test / playground / fork */
  type?: "test" | "playground" | "fork";
  parentId?: string | null;
  /** Set when this session is one variant of an experiment. */
  experimentId?: string | null;
  requiresTool?: boolean;
  pinned?: AgentSessionPin | null;
  source?: AgentSessionSource | null;
  messages: AgentTestMessage[];
}

export interface AgentTestDraftInput {
  prompt: string;
  level: number;
  tools: string[];
  requiresTool?: boolean;
}

export interface AgentTestInput {
  agentId: ID;
  content?: string;
  sessionId?: ID;
  draft?: AgentTestDraftInput;
  /** buyer: append the content as a buyer message; agent: run on current context. */
  inputMode?: "buyer" | "agent";
}

export interface AgentTestResult {
  session: AgentTestSession;
  reply?: AgentTestMessage;
  restricted?: boolean;
  reasoning?: string;
}

export interface ForkAgentSessionInput {
  agentId: ID;
  conversationId: number;
  messageIndex: number;
  versionKind?: "preset" | "draft";
  revisionId?: number | null;
  draft?: AgentTestDraftInput | null;
}

export interface AgentConsoleState {
  llmLevels: LlmLevelConfig[];
  agents: AgentConfig[];
  agentPresets?: AgentPreset[];
  systemAgents?: SystemAgentDefinition[];
  history: AgentTestSession[];
}
