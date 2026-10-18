"use client";

import { DeleteOutlined, EditOutlined, MessageOutlined, PlusOutlined, ReloadOutlined, SnippetsOutlined } from "@ant-design/icons";
import { Button, Card, Space, Switch, Tag, Tooltip, Typography } from "antd";
import Link from "next/link";
import { AppTable } from "@/components/AppTable";
import { StatusTag } from "@/components/StatusTag";
import { isSystemAgentModified } from "@/domain/agent/agentModel";
import { formatDateTime } from "@/domain/time";
import type { AgentConfig, SystemAgentDefinition } from "@/types/agent";

type AgentCategory = AgentConfig["category"];

export function AgentGroupTable({ category, agents, systemAgents, loading, isMutating, onToggle, onEdit, onCopy, onCreate, onDelete, onReset }: {
  category: AgentCategory;
  agents: AgentConfig[];
  systemAgents: SystemAgentDefinition[];
  loading: boolean;
  isMutating: (id: string) => boolean;
  onToggle: (agent: AgentConfig, enabled: boolean) => void;
  onEdit: (agent: AgentConfig) => void;
  onCopy: (agent: AgentConfig) => void;
  onCreate?: () => void;
  onDelete: (agent: AgentConfig) => void;
  onReset: (agent: AgentConfig) => void;
}) {
  const definitionOf = (agent: AgentConfig) => agent.apid ? systemAgents.find((item) => item.apid === agent.apid) : undefined;
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
          {
            title: "名称",
            dataIndex: "name",
            render: (name: string, record: AgentConfig) => (
              <Space size="small" wrap>
                <Typography.Text strong>{name}</Typography.Text>
                {category === "system" && isSystemAgentModified(record, definitionOf(record)) ? <Tag color="orange">已修改</Tag> : null}
              </Space>
            ),
          },
          { title: "类型", dataIndex: "category", render: (value: AgentCategory) => <StatusTag status={value} /> },
          ...(category === "regular" ? [{ title: "能力", dataIndex: "capabilities", render: (items: string[]) => <Space wrap>{items.map((item) => <Tag key={item}>{item}</Tag>)}</Space> }] : []),
          { title: "提示词", dataIndex: "prompt", width: 320, ellipsis: true },
          { title: "最近修改", dataIndex: "updatedAt", width: 160, render: (value: string) => value ? formatDateTime(value) : "—" },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 110,
            render: (enabled: boolean, record: AgentConfig) => category === "system"
              ? <Tooltip title="系统 Agent 始终启用，被对应工具直接调用"><Tag>恒启用</Tag></Tooltip>
              : <Switch checked={enabled} loading={isMutating(record.id)} onChange={(checked) => onToggle(record, checked)} />,
          },
          {
            title: "操作",
            width: 220,
            render: (_: unknown, record: AgentConfig) => (
              <Space size="small">
                <Button size="small" icon={<EditOutlined />} onClick={() => onEdit(record)}>编辑</Button>
                {category === "system" ? (
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => onReset(record)} loading={isMutating(record.id)}>重置</Button>
                ) : (
                  <>
                    <Button size="small" icon={<SnippetsOutlined />} onClick={() => onCopy(record)} loading={isMutating(record.id)}>复制</Button>
                    <Button size="small" danger icon={<DeleteOutlined />} onClick={() => onDelete(record)} loading={isMutating(record.id)}>删除</Button>
                  </>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}
