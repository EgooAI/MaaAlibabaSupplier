"use client";

import { DeleteOutlined, EditOutlined, MessageOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Space, Switch, Table, Tag } from "antd";
import Link from "next/link";
import { useState } from "react";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { HydrationSafeTable } from "@/components/HydrationSafeTable";
import { StatusTag } from "@/components/StatusTag";
import { displayAgentTools, filterAgentsByCategory } from "@/domain/agent/agentModel";
import type { AgentConfig, AgentEditValues } from "@/types/agent";
import { AgentEditModal } from "./AgentEditModal";
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

  async function handleSave(values: AgentEditValues) {
    if (editingAgent) {
      const ok = await workbench.saveAgent(editingAgent, values);
      if (ok) setEditingAgent(undefined);
      return;
    }
    if (createOpen) {
      const ok = await workbench.createAgent(values);
      if (ok) setCreateOpen(false);
    }
  }

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <AgentGroupTable category={category} agents={visibleAgents} loading={workbench.loading} mutationId={workbench.agentMutationId} onToggle={workbench.toggleAgent} onEdit={setEditingAgent} onCreate={category === "regular" ? () => setCreateOpen(true) : undefined} onDelete={(agent) => setPendingAgentAction({ agent, type: "delete" })} onReset={(agent) => setPendingAgentAction({ agent, type: "reset" })} />

      <AgentEditModal agent={editingAgent} category={category} title={createOpen ? "新增普通 Agent" : undefined} open={Boolean(editingAgent) || createOpen} saving={Boolean(workbench.agentMutationId)} onClose={() => { setEditingAgent(undefined); setCreateOpen(false); }} onSave={handleSave} />
      <ActionConfirmModal
        title={pendingAgentAction?.type === "reset" ? "重置系统 Agent" : "删除普通 Agent"}
        open={Boolean(pendingAgentAction)}
        warning={pendingAgentAction?.type === "reset" ? "确认将该系统 Agent 恢复为默认配置？" : "确认删除该普通 Agent？删除后无法恢复。"}
        okText={pendingAgentAction?.type === "reset" ? "重置" : "删除"}
        loading={Boolean(pendingAgentAction && workbench.agentMutationId === pendingAgentAction.agent.id)}
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

function AgentGroupTable({ category, agents, loading, mutationId, onToggle, onEdit, onCreate, onDelete, onReset }: { category: AgentCategory; agents: AgentConfig[]; loading: boolean; mutationId?: string; onToggle: (agent: AgentConfig, enabled: boolean) => void; onEdit: (agent: AgentConfig) => void; onCreate?: () => void; onDelete: (agent: AgentConfig) => void; onReset: (agent: AgentConfig) => void }) {
  const extra = category === "regular" ? (
    <Space size="small">
      {onCreate ? <Button size="small" type="primary" icon={<PlusOutlined />} onClick={onCreate}>新增 Agent</Button> : null}
      <Link href="/chat/agent-sessions">
        <Button size="small" icon={<MessageOutlined />}>对话测试</Button>
      </Link>
    </Space>
  ) : undefined;

  return (
    <Card extra={extra}>
      <Table
        rowKey="id"
        dataSource={agents}
        loading={loading}
        components={{ table: HydrationSafeTable }}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "类型", dataIndex: "category", render: (value: AgentCategory) => <StatusTag status={value} /> },
          ...(category === "regular" ? [{ title: "能力", dataIndex: "capabilities", render: (items: string[]) => <Space wrap>{displayAgentTools(items).map((item) => <Tag key={item}>{item}</Tag>)}</Space> }] : []),
          { title: "提示词", dataIndex: "prompt", width: 360, ellipsis: true },
          { title: "启用", dataIndex: "enabled", render: (enabled: boolean, record: AgentConfig) => <Switch checked={enabled} loading={mutationId === record.id} onChange={(checked) => onToggle(record, checked)} /> },
          {
            title: "操作",
            render: (_: unknown, record: AgentConfig) => (
              <Space size="small">
                <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(record)}>编辑</Button>
                {category === "system" ? (
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => onReset(record)} loading={mutationId === record.id}>重置</Button>
                ) : (
                  <Button size="small" danger icon={<DeleteOutlined />} onClick={() => onDelete(record)} loading={mutationId === record.id}>删除</Button>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
