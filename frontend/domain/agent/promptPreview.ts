/**
 * Prompt size hints for the editor.
 *
 * Rendering prompt text lives in `domain/render/markdown` (kind `markdown`);
 * this module only measures the draft.
 */

export function promptCharCount(raw: string | null | undefined) {
  return (raw ?? "").length;
}

/**
 * Rough token estimate for a mixed CJK/latin/other prompt.
 *
 * Used only as an editor hint; real limits are enforced by the model context
 * window. CJK characters count as one token each, other text as ~4 chars each.
 */
export function estimatePromptTokens(raw: string | null | undefined) {
  const value = raw ?? "";
  const cjk = value.match(/[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/g)?.length ?? 0;
  return cjk + Math.ceil((value.length - cjk) / 4);
}
