"use client";

import { DeleteOutlined, ExperimentOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Col, Empty, Row, Space, Table, Typography } from "antd";
import { useState } from "react";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { HydrationSafeTable } from "@/components/HydrationSafeTable";
import { StatusTag } from "@/components/StatusTag";
import type { HealthModule, TaskItem } from "@/types/status";
import { useStatusWorkbench } from "./hooks/useStatusWorkbench";

const healthModuleIds = ["health-identity", "health-proxy", "health-receiver", "health-node"] as const;

export function StatusPage() {
  const { snapshot, loading, refreshing, creatingTask, refresh, createTestTask, deleteTask, deletingTaskId } = useStatusWorkbench();
  const [pendingDeleteTask, setPendingDeleteTask] = useState<TaskItem>();
  const modules = healthModuleIds.map((id) => snapshot?.modules.find((module) => module.id === id)).filter((module): module is HealthModule => Boolean(module));

  async function handleDeleteConfirm() {
    if (!pendingDeleteTask) return;
    const deleted = await deleteTask(pendingDeleteTask.id);
    if (deleted) setPendingDeleteTask(undefined);
  }

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <div>
        <Typography.Title level={2} className="!mb-1">系统状态</Typography.Title>
      </div>

      <Card title="系统状态" loading={loading}>
        <Row gutter={[16, 16]}>
          {modules.map((module) => <Col xs={24} md={8} key={module.id}><HealthModulePanel module={module} /></Col>)}
          {!loading && !modules.length ? <Col span={24}><Empty description="暂无状态数据" /></Col> : null}
        </Row>
      </Card>

      <Card title="快捷测试动作">
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={refresh}>刷新运行状态</Button>
          <Button type="primary" icon={<ExperimentOutlined />} loading={creatingTask} onClick={createTestTask}>创建测试任务</Button>
        </Space>
      </Card>

      <Card title="任务队列">
        <Table
          rowKey="id"
          dataSource={snapshot?.tasks ?? []}
          loading={loading}
          scroll={{ x: 900 }}
          components={{ table: HydrationSafeTable }}
          columns={[
            { title: "任务 ID", dataIndex: "id", width: 150 },
            { title: "操作类型", dataIndex: "type", width: 180 },
            { title: "目标", dataIndex: "target", width: 180, render: (value: string | undefined) => value || "—" },
            { title: "创建时间", dataIndex: "createdAt", width: 180 },
            { title: "状态", dataIndex: "status", width: 120, render: (value: TaskItem["status"]) => <StatusTag status={value} /> },
            { title: "任务消息", dataIndex: "message", minWidth: 220 },
            { title: "结果", dataIndex: "result", minWidth: 220, render: (value: string | undefined, task: TaskItem) => value ?? (task.status === "succeeded" || task.status === "failed" ? "—" : "未完成") },
            {
              title: "操作",
              key: "action",
              width: 100,
              render: (_: unknown, task: TaskItem) => (
                <Button danger type="link" icon={<DeleteOutlined />} onClick={() => setPendingDeleteTask(task)}>
                  删除
                </Button>
              ),
            },
          ]}
          locale={{ emptyText: "暂无任务" }}
        />
      </Card>

      <ActionConfirmModal
        title="删除任务"
        open={Boolean(pendingDeleteTask)}
        warning="确认删除以下任务？删除后无法恢复。"
        okText="删除"
        loading={Boolean(pendingDeleteTask && deletingTaskId === pendingDeleteTask.id)}
        onCancel={() => setPendingDeleteTask(undefined)}
        onConfirm={handleDeleteConfirm}
        details={pendingDeleteTask ? [
          { label: "任务 ID", value: pendingDeleteTask.id },
          { label: "操作类型", value: pendingDeleteTask.type },
          { label: "状态", value: pendingDeleteTask.status },
          { label: "创建时间", value: pendingDeleteTask.createdAt },
          { label: "任务消息", value: pendingDeleteTask.message, span: 2 },
          { label: "结果", value: pendingDeleteTask.result ?? "—", span: 2 },
        ] : undefined}
      />
    </Space>
  );
}

function HealthModulePanel({ module }: { module: HealthModule }) {
  const title = module.id === "health-identity" ? "用户状态" : module.id === "health-proxy" ? "MITM 代理" : "MITM Receiver";
  const statusText = module.status === "healthy" ? "在线" : module.status === "warning" ? "注意" : "离线";

  return (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <Typography.Text strong>{title}</Typography.Text>
        <StatusTag status={module.status} />
      </div>
      <Typography.Title level={4} className="!mb-2">{statusText}</Typography.Title>
      <Space orientation="vertical" size={4}>
        <Typography.Text>{module.description}</Typography.Text>
        <Typography.Text>延迟：{module.latency === null ? "未知" : `${module.latency}ms`}</Typography.Text>
      </Space>
    </div>
  );
}
