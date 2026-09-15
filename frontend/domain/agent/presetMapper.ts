import type { AgentConfig, AgentEditValues, AgentPreset, DbAgentPreset } from "@/types/agent";
import { normalizeAgentLevel } from "./llmModel";
import { normalizeAgentTools } from "./toolModel";

export const SYSTEM_AGENT_APIDS = {
  translation: "agent-1bad27aabaac439da678f31d53855b5d",
  replySuggestion: "agent-5a43bda9e1304108a1a78a3575a44e27",
  stageAnalysis: "agent-f6fb1e0ddff44d27bb3e19e243a70584",
  intentAnalysis: "agent-c9b80fdfad234392b55d84de93a186ae",
} as const;

export function isSystemAgentApid(apid: string) {
  return Object.values(SYSTEM_AGENT_APIDS).includes(apid as (typeof SYSTEM_AGENT_APIDS)[keyof typeof SYSTEM_AGENT_APIDS]);
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

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown) {
  return typeof value === "number" ? value : Number.NaN;
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
