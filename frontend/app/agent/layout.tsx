"use client";

import { AgentWorkbenchProvider } from "@/features/agent/hooks/useAgentWorkbench";
import type { ReactNode } from "react";

export default function AgentLayout({ children }: { children: ReactNode }) {
  return <AgentWorkbenchProvider>{children}</AgentWorkbenchProvider>;
}
