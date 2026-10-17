import { diffChars, diffLines, diffWordsWithSpace } from "diff";

export type PromptDiffSegment = { kind: "same" | "added" | "removed"; text: string };

export type PromptDiffRow = {
  kind: "context" | "changed" | "added" | "removed";
  before: string | null;
  after: string | null;
  beforeSegments?: PromptDiffSegment[];
  afterSegments?: PromptDiffSegment[];
};

export type PromptDiff = {
  rows: PromptDiffRow[];
  addedLines: number;
  removedLines: number;
};

const CJK_PATTERN = /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/;

type LineToken = { kind: "same" | "added" | "removed"; text: string };

function normalizeTrailingNewline(value: string) {
  return value === "" || value.endsWith("\n") ? value : `${value}\n`;
}

function toLineTokens(before: string, after: string): LineToken[] {
  const tokens: LineToken[] = [];
  for (const part of diffLines(normalizeTrailingNewline(before), normalizeTrailingNewline(after))) {
    const kind: LineToken["kind"] = part.added ? "added" : part.removed ? "removed" : "same";
    const lines = part.value.split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
    for (const text of lines) tokens.push({ kind, text });
  }
  return tokens;
}

function segmentLinePair(before: string, after: string) {
  const parts = CJK_PATTERN.test(before + after)
    ? diffChars(before, after)
    : diffWordsWithSpace(before, after);
  const beforeSegments: PromptDiffSegment[] = [];
  const afterSegments: PromptDiffSegment[] = [];
  for (const part of parts) {
    if (part.added) {
      afterSegments.push({ kind: "added", text: part.value });
    } else if (part.removed) {
      beforeSegments.push({ kind: "removed", text: part.value });
    } else {
      beforeSegments.push({ kind: "same", text: part.value });
      afterSegments.push({ kind: "same", text: part.value });
    }
  }
  return { before: beforeSegments, after: afterSegments };
}

/**
 * Line-level diff aligned into rows; paired changed lines carry word-level
 * (character-level for CJK) segments so prompt edits stay readable.
 */
export function buildPromptDiff(before: string, after: string): PromptDiff {
  const tokens = toLineTokens(before, after);
  const rows: PromptDiffRow[] = [];
  let addedLines = 0;
  let removedLines = 0;
  let index = 0;

  while (index < tokens.length) {
    if (tokens[index].kind === "same") {
      rows.push({ kind: "context", before: tokens[index].text, after: tokens[index].text });
      index += 1;
      continue;
    }
    const removed: string[] = [];
    const added: string[] = [];
    while (index < tokens.length && tokens[index].kind === "removed") removed.push(tokens[index++].text);
    while (index < tokens.length && tokens[index].kind === "added") added.push(tokens[index++].text);
    const paired = Math.min(removed.length, added.length);
    for (let pair = 0; pair < paired; pair += 1) {
      const segments = segmentLinePair(removed[pair], added[pair]);
      rows.push({
        kind: "changed",
        before: removed[pair],
        after: added[pair],
        beforeSegments: segments.before,
        afterSegments: segments.after,
      });
      addedLines += 1;
      removedLines += 1;
    }
    for (let extra = paired; extra < removed.length; extra += 1) {
      rows.push({ kind: "removed", before: removed[extra], after: null });
      removedLines += 1;
    }
    for (let extra = paired; extra < added.length; extra += 1) {
      rows.push({ kind: "added", before: null, after: added[extra] });
      addedLines += 1;
    }
  }
  return { rows, addedLines, removedLines };
}

export function hasPromptChanges(before: string, after: string) {
  return before !== after;
}
