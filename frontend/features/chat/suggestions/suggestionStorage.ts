import type { DbAgentPreset } from "@/types/agent";
import type { SuggestionDraft, SuggestionRunInput } from "@/types/suggestions";

export type PromptDraft = SuggestionDraft & { baseFingerprint?: string };
type Scope = { self_ali_id: string; data_dir: string };

export const suggestionKey = (scope: Scope, name: string) => `maa:suggestions:${JSON.stringify([scope.data_dir, scope.self_ali_id, name])}`;
export const draftOf = (preset: DbAgentPreset): PromptDraft => ({
  prompt: preset.prompt, level: preset.intelevel, tools: [...preset.tools],
  requires_tool: Boolean(preset.requires_tool), baseFingerprint: preset.fingerprint,
});
export const runDraft = ({ prompt, level, tools, requires_tool }: PromptDraft): SuggestionDraft => ({ prompt, level, tools, requires_tool });
export const sameDraft = (left: SuggestionDraft, right: SuggestionDraft) => JSON.stringify(runDraft(left)) === JSON.stringify(runDraft(right));

export function readDraft(key: string): PromptDraft | undefined {
  const value = JSON.parse(localStorage.getItem(key) ?? "null");
  if (!value || typeof value.prompt !== "string" || !Number.isInteger(value.level)
    || !Array.isArray(value.tools) || !value.tools.every((tool: unknown) => typeof tool === "string")
    || typeof value.requires_tool !== "boolean") return undefined;
  return value;
}

export function readPending(key: string): SuggestionRunInput | undefined {
  const value = JSON.parse(localStorage.getItem(key) ?? "null");
  if (!value || typeof value.runId !== "string" || typeof value.agentId !== "string"
    || !(value.contextRunId === null || typeof value.contextRunId === "string")) return undefined;
  return value;
}
