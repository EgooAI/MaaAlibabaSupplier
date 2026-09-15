import type { DocumentLlmConfig, LlmLevelConfig } from "@/types/agent";

export function isAgentLevel(value: number) {
  return Number.isInteger(value) && value >= 0 && value <= 4;
}

export function normalizeAgentLevel(value: number) {
  if (!isAgentLevel(value)) throw new Error("Agent 等级必须是 0 到 4 的整数");
  return value;
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
