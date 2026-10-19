import type { ReactNode } from "react";
import type { AgentToolCallRecord, AgentTurnIntent } from "@/types/agent";

export type ToolRendererProps = {
  call: AgentToolCallRecord;
  intent?: AgentTurnIntent;
  /** Simulated turns never queue a send. */
  simulated: boolean;
};

export type ToolRenderer = {
  /** Icon element for the card header. */
  icon: ReactNode;
  /** Summarize the arguments on the collapsed card. */
  summarize?: (args: Record<string, unknown>) => string;
  /** Result body; the card keeps the collapse/expand chrome itself. */
  render: (props: ToolRendererProps) => ReactNode;
};

const registry = new Map<string, ToolRenderer>();

/** Register a dedicated renderer; unknown tools use the generic JSON result. */
export function registerToolRenderer(name: string, renderer: ToolRenderer) {
  registry.set(name, renderer);
}

export function toolRendererOf(name: string): ToolRenderer | undefined {
  return registry.get(name);
}
