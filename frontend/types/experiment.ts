import type { ID } from "@/types/common";
import type { AgentTestDraftInput, AgentTestSession } from "@/types/agent";

/** One scripted input every variant receives at the same position. */
export interface ExperimentStep {
  index: number;
  /** buyer: send text as the buyer's next message; agent: continue unprompted. */
  mode: "buyer" | "agent";
  text: string;
  label: string;
}

export interface ExperimentAnnotation {
  step: number;
  variantId: string;
  verdict: "" | "better" | "worse";
  note: string;
}

export type ExperimentSourceKind = "conversation" | "session" | "text";

export interface ExperimentSource {
  kind?: ExperimentSourceKind;
  conversationId?: number | null;
  messageIndex?: number | null;
  conversationTitle?: string;
  frozenChars?: number;
  sessionId?: string | null;
}

/** A variant as a full session, plus the label the experiment gave it. */
export interface ExperimentVariant extends AgentTestSession {
  label: string;
}

export interface ExperimentVariantSummary {
  id: ID;
  label: string;
  turnCount: number;
  draft: boolean;
  pinned: AgentTestSession["pinned"];
}

export interface StepProgress {
  variants?: string[];
  /** Turn count each variant had before the step was first applied. */
  before?: Record<string, number>;
}

export interface Experiment {
  id: ID;
  agentId: ID;
  name: string;
  frozenPrefix: string;
  source: ExperimentSource;
  steps: ExperimentStep[];
  annotations: ExperimentAnnotation[];
  notes: string;
  progress: Record<string, StepProgress>;
  createdAt: string;
  updatedAt: string;
  activeRunId: string | null;
  variants: ExperimentVariant[];
}

export interface ExperimentSummary extends Omit<Experiment, "variants"> {
  variants: ExperimentVariantSummary[];
}

/* --- requests ------------------------------------------------------------ */

export interface ExperimentVariantInput {
  label?: string;
  versionKind: "preset" | "draft";
  revisionId?: number | null;
  draft?: AgentTestDraftInput | null;
}

export interface CreateExperimentSourceInput {
  kind: ExperimentSourceKind;
  conversationId?: number;
  messageIndex?: number;
  sessionId?: string;
  text?: string;
}

export interface CreateExperimentInput {
  agentId: ID;
  name?: string;
  source: CreateExperimentSourceInput;
  variants: ExperimentVariantInput[];
}

/** Rename, re-noted or re-annotated: omitted fields keep their stored value. */
export interface UpdateExperimentInput {
  name?: string;
  notes?: string;
  annotations?: ExperimentAnnotation[];
}

/* --- runs ---------------------------------------------------------------- */

export type RunStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface VariantRunStatus {
  variantId: string;
  status: RunStatus;
  error: string | null;
  durationMs: number;
}

export interface RunSnapshot {
  runId: string;
  experimentId: string;
  stepIndex: number;
  status: RunStatus;
  startedAt: number;
  finishedAt: number | null;
  variants: VariantRunStatus[];
}

/** Terminal states stop polling. */
export function isRunFinished(status: RunStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled" || status === "interrupted";
}
