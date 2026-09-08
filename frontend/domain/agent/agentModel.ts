import type { AgentConfig, AgentEditValues, AgentPreset, AgentTestMessage, AgentTestSession, DbAgentPreset, DocumentLlmConfig, LlmLevelConfig } from "@/types/agent";

export type AgentTestTurn = {
  user: AgentTestMessage;
  assistant: AgentTestMessage;
  startIndex: number;
  endIndex: number;
};

export const SYSTEM_AGENT_APIDS = {
  translation: "agent-1bad27aabaac439da678f31d53855b5d",
  replySuggestion: "agent-5a43bda9e1304108a1a78a3575a44e27",
  stageAnalysis: "agent-f6fb1e0ddff44d27bb3e19e243a70584",
  intentAnalysis: "agent-c9b80fdfad234392b55d84de93a186ae",
} as const;

export const AGENT_TOOL_OPTIONS = [
  { label: "CRM 查询", value: "crm_query" },
  { label: "报价模板", value: "quote_template" },
  { label: "订单摘要", value: "order_summary" },
  { label: "物流计算", value: "logistics_calculator" },
] as const;

const TOOL_LABELS = new Map<string, string>(AGENT_TOOL_OPTIONS.map((option) => [option.value, option.label]));
const TOOL_NAMES = new Map<string, string>(AGENT_TOOL_OPTIONS.map((option) => [option.label, option.value]));

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

export function formatAgentSessionDate(createdAt: string) {
  return createdAt.slice(5, 16).replace("T", " ");
}

export function isAgentLevel(value: number) {
  return Number.isInteger(value) && value >= 0 && value <= 4;
}

export function normalizeAgentLevel(value: number) {
  if (!isAgentLevel(value)) throw new Error("Agent 等级必须是 0 到 4 的整数");
  return value;
}

export function agentToolToDisplayLabel(tool: string) {
  return TOOL_LABELS.get(tool) ?? tool;
}

export function agentToolToRegisteredName(tool: string) {
  return TOOL_NAMES.get(tool) ?? tool;
}

export function normalizeAgentTools(tools: string[] = []) {
  const names = tools.map((tool) => agentToolToRegisteredName(tool.trim())).filter(Boolean);
  return Array.from(new Set(names));
}

export function displayAgentTools(tools: string[] = []) {
  return normalizeAgentTools(tools).map(agentToolToDisplayLabel);
}

export function agentPresetToDbPreset(preset: AgentPreset): DbAgentPreset {
  return {
    apid: preset.id,
    name: preset.name,
    description: preset.description,
    prompt: preset.prompt,
    intelevel: normalizeAgentLevel(preset.level),
    tools: normalizeAgentTools(preset.tools),
    enabled: preset.enabled,
    updated_at: preset.updated_at,
    category: preset.category,
  };
}

export function dbPresetToAgentPreset(input: DbAgentPreset, current?: AgentPreset, updatedAt = input.updated_at): AgentPreset {
  return {
    id: input.apid,
    name: input.name,
    category: input.category ?? current?.category ?? (isSystemAgentApid(input.apid) ? "system" : "regular"),
    enabled: input.enabled,
    description: input.description,
    prompt: input.prompt,
    level: normalizeAgentLevel(input.intelevel),
    tools: normalizeAgentTools(input.tools),
    updated_at: updatedAt,
  };
}

export function agentPresetToConfig(preset: AgentPreset): AgentConfig {
  const id = preset.id;
  return {
    id,
    name: preset.name,
    category: preset.category,
    enabled: preset.enabled,
    capabilities: normalizeAgentTools(preset.tools),
    description: preset.description,
    updatedAt: preset.updated_at,
    prompt: preset.prompt,
    level: normalizeAgentLevel(preset.level),
    apid: id,
  };
}

export function isSystemAgentApid(apid: string) {
  return Object.values(SYSTEM_AGENT_APIDS).includes(apid as (typeof SYSTEM_AGENT_APIDS)[keyof typeof SYSTEM_AGENT_APIDS]);
}

export function normalizeAgentEditValues(values: AgentEditValues) {
  const name = stringValue(values.name).trim();
  const prompt = stringValue(values.prompt).trim();
  if (!name) throw new Error("请输入名称");
  if (!prompt) throw new Error("请输入提示词");
  return {
    name,
    description: stringValue(values.description).trim(),
    prompt,
    level: normalizeAgentLevel(numberValue(values.level)),
    capabilities: normalizeAgentTools(values.capabilities),
  };
}

export function agentConfigToPreset(agent: AgentConfig, values: AgentEditValues, updatedAt: string): AgentPreset {
  const normalized = normalizeAgentEditValues(values);
  return {
    id: agent.id,
    name: normalized.name,
    category: agent.category,
    enabled: agent.enabled,
    description: normalized.description,
    prompt: normalized.prompt,
    level: normalized.level,
    tools: normalized.capabilities,
    updated_at: updatedAt,
  };
}

export function createRegularAgentPreset(values: AgentEditValues, updatedAt: string, id = `agent-${Date.now()}`): AgentPreset {
  const normalized = normalizeAgentEditValues(values);
  return {
    id,
    name: normalized.name,
    category: "regular",
    enabled: true,
    description: normalized.description,
    prompt: normalized.prompt,
    level: normalized.level,
    tools: normalized.capabilities,
    updated_at: updatedAt,
  };
}

export function isValidToolRoundLimit(value: number | null | undefined) {
  return value === null || value === undefined || (Number.isInteger(value) && value > 0);
}

export function normalizeToolRoundLimit(value: number | null | undefined) {
  if (!isValidToolRoundLimit(value)) throw new Error("最大工具轮数必须为空或正整数");
  return value ?? null;
}

export function documentToLlmLevelConfig(input: DocumentLlmConfig): LlmLevelConfig {
  return {
    level: normalizeAgentLevel(input.level),
    baseUrl: input.base_url,
    apiKey: input.api_key,
    modelName: input.model_name,
    systemPrompt: input.system_prompt,
    context: input.context,
    maxToolRounds: normalizeToolRoundLimit(input.max_tool_rounds),
  };
}

export function llmLevelToDocumentConfig(config: LlmLevelConfig): DocumentLlmConfig {
  return {
    level: normalizeAgentLevel(config.level),
    base_url: config.baseUrl,
    api_key: config.apiKey,
    model_name: config.modelName,
    system_prompt: config.systemPrompt,
    context: config.context,
    max_tool_rounds: normalizeToolRoundLimit(config.maxToolRounds),
  };
}

export function upsertLlmLevel(levels: LlmLevelConfig[], next: LlmLevelConfig) {
  return levels.some((level) => level.level === next.level)
    ? levels.map((level) => level.level === next.level ? next : level)
    : [...levels, next].sort((a, b) => a.level - b.level);
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown) {
  return typeof value === "number" ? value : Number.NaN;
}
