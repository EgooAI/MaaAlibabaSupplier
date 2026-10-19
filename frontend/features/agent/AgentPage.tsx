"use client";

import { Space } from "antd";
import { useState } from "react";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { filterAgentsByCategory } from "@/domain/agent/agentModel";
import type { AgentConfig, AgentEditValues, AgentSaveOptions } from "@/types/agent";
import { AgentEditDrawer } from "./AgentEditDrawer";
import { AgentGroupTable } from "./components/AgentGroupTable";
import { useAgentWorkbench } from "./hooks/useAgentWorkbench";

type AgentCategory = AgentConfig["category"];

type AgentPageProps = {
  category: AgentCategory;
};

type PendingAgentAction = {
  agent: AgentConfig;
  type: "delete" | "reset";
};

export function AgentPage({ category }: AgentPageProps) {
  const workbench = useAgentWorkbench();
  const agents = workbench.state?.agents ?? [];
  const systemAgents = workbench.state?.systemAgents ?? [];
  const tools = workbench.tools ?? [];
  const llmLevels = workbench.state?.llmLevels ?? [];
  const visibleAgents = filterAgentsByCategory(agents, category);
  const [editingAgent, setEditingAgent] = useState<AgentConfig>();
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingAgentAction, setPendingAgentAction] = useState<PendingAgentAction>();

  async function handleAgentAction() {
    if (!pendingAgentAction) return;
    const ok = pendingAgentAction.type === "delete"
      ? await workbench.deleteAgent(pendingAgentAction.agent)
      : await workbench.resetSystemAgent(pendingAgentAction.agent);
    if (ok) setPendingAgentAction(undefined);
  }

  async function handleSave(values: AgentEditValues, options?: AgentSaveOptions) {
    if (editingAgent) {
      const ok = await workbench.saveAgent(editingAgent, values, options);
      if (ok) setEditingAgent(undefined);
      return;
    }
    if (createOpen) {
      const ok = await workbench.createAgent(values, options);
      if (ok) setCreateOpen(false);
    }
  }

  const isMutating = (id: string) => workbench.isAgentMutating(id);

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <AgentGroupTable
        category={category}
        agents={visibleAgents}
        systemAgents={systemAgents}
        loading={workbench.loading}
        isMutating={isMutating}
        onToggle={workbench.toggleAgent}
        onEdit={setEditingAgent}
        onCopy={(agent) => void workbench.copyAgent(agent)}
        onCreate={category === "regular" ? () => setCreateOpen(true) : undefined}
        onDelete={(agent) => setPendingAgentAction({ agent, type: "delete" })}
        onReset={(agent) => setPendingAgentAction({ agent, type: "reset" })}
      />

      <AgentEditDrawer
        agent={editingAgent}
        category={category}
        systemAgents={systemAgents}
        tools={tools}
        llmLevels={llmLevels}
        title={createOpen ? "新增普通 Agent" : undefined}
        open={Boolean(editingAgent) || createOpen}
        saving={workbench.mutatingIds.size > 0}
        onClose={() => {
          setEditingAgent(undefined);
          setCreateOpen(false);
        }}
        onSave={handleSave}
      />
      <ActionConfirmModal
        title={pendingAgentAction?.type === "reset" ? "重置系统 Agent" : "删除普通 Agent"}
        open={Boolean(pendingAgentAction)}
        warning={pendingAgentAction?.type === "reset" ? "确认将该系统 Agent 恢复为默认配置？" : "确认删除该普通 Agent？删除后无法恢复。"}
        okText={pendingAgentAction?.type === "reset" ? "重置" : "删除"}
        loading={Boolean(pendingAgentAction && isMutating(pendingAgentAction.agent.id))}
        onCancel={() => setPendingAgentAction(undefined)}
        onConfirm={handleAgentAction}
        details={pendingAgentAction ? [
          { label: "Agent 名称", value: pendingAgentAction.agent.name },
          { label: "类型", value: pendingAgentAction.agent.category === "system" ? "系统 Agent" : "普通 Agent" },
          { label: "描述", value: pendingAgentAction.agent.description || "暂无描述", span: 2 },
        ] : undefined}
      />
    </Space>
  );
}
