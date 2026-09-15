"use client";

import { DeleteOutlined, EditOutlined, MessageOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Space, Switch, Tag } from "antd";
import Link from "next/link";
import { AppTable } from "@/components/AppTable";
import { StatusTag } from "@/components/StatusTag";
import { displayAgentTools } from "@/domain/agent/agentModel";
import type { AgentConfig } from "@/types/agent";

type AgentCategory = AgentConfig["category"];

export function AgentGroupTable({ category, agents, loading, isMutating, onToggle, onEdit, onCreate, onDelete, onReset }: {
  category: AgentCategory;
  agents: AgentConfig[];
  loading: boolean;
  isMutating: (id: string) => boolean;
  onToggle: (agent: AgentConfig, enabled: boolean) => void;
  onEdit: (agent: AgentConfig) => void;
  onCreate?: () => void;
  onDelete: (agent: AgentConfig) => void;
  onReset: (agent: AgentConfig) => void;
}) {
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
      <AppTable
        rowKey="id"
        dataSource={agents}
        loading={loading}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "类型", dataIndex: "category", render: (value: AgentCategory) => <StatusTag status={value} /> },
          ...(category === "regular" ? [{ title: "能力", dataIndex: "capabilities", render: (items: string[]) => <Space wrap>{displayAgentTools(items).map((item) => <Tag key={item}>{item}</Tag>)}</Space> }] : []),
          { title: "提示词", dataIndex: "prompt", width: 360, ellipsis: true },
          { title: "启用", dataIndex: "enabled", render: (enabled: boolean, record: AgentConfig) => <Switch checked={enabled} loading={isMutating(record.id)} onChange={(checked) => onToggle(record, checked)} /> },
          {
            title: "操作",
            render: (_: unknown, record: AgentConfig) => (
              <Space size="small">
                <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(record)}>编辑</Button>
                {category === "system" ? (
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => onReset(record)} loading={isMutating(record.id)}>重置</Button>
                ) : (
                  <Button size="small" danger icon={<DeleteOutlined />} onClick={() => onDelete(record)} loading={isMutating(record.id)}>删除</Button>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
