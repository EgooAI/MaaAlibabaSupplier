import type { AgentTestMessage, AgentTestSession } from "@/types/agent";
import type { Experiment } from "@/types/experiment";

/** The transcript of one step in one variant: the buyer input and the reply it caused. */
export type LocatedStepTurns = {
  /** Absent for agent-mode steps: they add no buyer message. */
  user?: AgentTestMessage;
  assistant?: AgentTestMessage;
};

/** What the composer's primary button does next. */
export type ComposerAction =
  | { kind: "append"; stepIndex: number }
  | { kind: "continue"; stepIndex: number }
  | { kind: "run"; stepIndex: number };

/** Steps in transcript order: the stored array order is not trusted. */
export function orderedSteps(experiment: Pick<Experiment, "steps">) {
  return [...experiment.steps].sort((left, right) => left.index - right.index);
}

/** Whether one variant has finished one step, per the experiment's own progress record. */
export function stepDoneFor(experiment: Pick<Experiment, "progress">, stepIndex: number, variantId: string) {
  return Boolean(experiment.progress[String(stepIndex)]?.variants?.includes(variantId));
}

/**
 * The turn the step starts at in this variant's transcript.
 *
 * ``progress[step].before`` records the turn count before the step first ran, so
 * it is exact even after a retry (which truncates back to it). Legacy records
 * without it fall back to the ordinal position the step would occupy: every
 * earlier finished step appended one buyer turn (two messages) or one agent
 * turn (one message).
 */
function stepStart(session: AgentTestSession, stepIndex: number, experiment: Experiment) {
  const recorded = experiment.progress[String(stepIndex)]?.before?.[session.id];
  if (typeof recorded === "number") {
    return Math.min(Math.max(recorded, 0), session.messages.length);
  }
  let cursor = 0;
  for (const step of orderedSteps(experiment)) {
    if (step.index >= stepIndex) break;
    if (stepDoneFor(experiment, step.index, session.id)) {
      cursor += step.mode === "buyer" ? 2 : 1;
    }
  }
  return Math.min(cursor, session.messages.length);
}

/**
 * Locates the turns one step produced in one variant's transcript.
 *
 * Returns ``null`` when the variant has not finished the step, so the board can
 * show a placeholder instead of borrowing a neighbouring turn.
 */
export function locateStepTurns(
  session: AgentTestSession,
  stepIndex: number,
  experiment: Experiment,
): LocatedStepTurns | null {
  const step = experiment.steps.find((item) => item.index === stepIndex);
  if (!step || !stepDoneFor(experiment, stepIndex, session.id)) return null;
  const start = stepStart(session, stepIndex, experiment);
  return step.mode === "buyer" ? buyerTurnFrom(session.messages, start) : assistantFrom(session.messages, start);
}

function buyerTurnFrom(messages: AgentTestMessage[], from: number): LocatedStepTurns | null {
  for (let index = from; index < messages.length; index += 1) {
    const message = messages[index];
    if (message.role !== "user") continue;
    const reply = messages.slice(index + 1).find((item) => item.role === "assistant");
    return reply ? { user: message, assistant: reply } : { user: message };
  }
  return null;
}

function assistantFrom(messages: AgentTestMessage[], from: number): LocatedStepTurns | null {
  for (let index = from; index < messages.length; index += 1) {
    if (messages[index].role === "assistant") return { assistant: messages[index] };
  }
  return null;
}

/**
 * The next thing the composer runs: append a step, continue a half-finished one,
 * or start the earliest step that is still incomplete.
 */
export function resolveComposerAction(experiment: Experiment): ComposerAction {
  const steps = orderedSteps(experiment);
  if (!steps.length) return { kind: "append", stepIndex: 0 };
  const total = experiment.variants.length;
  const pending = steps.find(
    (step) => (experiment.progress[String(step.index)]?.variants ?? []).length < total,
  );
  if (!pending) return { kind: "append", stepIndex: steps[steps.length - 1].index + 1 };
  const started = (experiment.progress[String(pending.index)]?.variants ?? []).length > 0;
  return started
    ? { kind: "continue", stepIndex: pending.index }
    : { kind: "run", stepIndex: pending.index };
}
