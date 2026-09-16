"use client";

import { ExperimentOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Col, Empty, Row, Space, Typography } from "antd";
import { AppTable } from "@/components/AppTable";
import { StatusTag } from "@/components/StatusTag";
import { HEALTH_MODULE_IDS, HEALTH_MODULE_TITLES } from "@/domain/status/statusModel";
import type { HealthModule, TaskItem } from "@/types/status";
import { useStatusWorkbench } from "./hooks/useStatusWorkbench";

export function StatusPage() {
  const { snapshot, loading, refreshing, creatingTask, testingNode, refresh, createTestTask, runNodeTest } = useStatusWorkbench();

  if (!snapshot) {
    return (
      <Space orientation="vertical" size="large" className="w-full">
        <Card loading={loading}>
          {!loading ? <Empty description="暂无状态数据" /> : null}
        </Card>
      </Space>
    );
  }

  const modules = HEALTH_MODULE_IDS.map((id) => snapshot.modules.find((module) => module.id === id)).filter((module): module is HealthModule => Boolean(module));

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <Card loading={loading}>
        <Row gutter={[16, 16]}>
          {modules.map((module) => <Col xs={24} md={8} key={module.id}><HealthModulePanel module={module} /></Col>)}
        </Row>
      </Card>

      <Card title="快捷测试动作">
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={refresh}>刷新运行状态</Button>
          <Button type="primary" icon={<ExperimentOutlined />} loading={creatingTask} onClick={createTestTask}>创建测试任务</Button>
          <Button loading={testingNode === "ChatInput_GoToInput"} onClick={() => void runNodeTest("ChatInput_GoToInput")}>检查聊天输入框</Button>
          <Button loading={testingNode === "ContactSearch_GoToSearch"} onClick={() => void runNodeTest("ContactSearch_GoToSearch")}>检查联系人搜索框</Button>
        </Space>
        <Typography.Paragraph type="secondary" className="mt-3 mb-0">界面检查会排队执行，仅识别控件，不点击或输入。</Typography.Paragraph>
      </Card>

      <Card title="任务队列">
        <AppTable
          rowKey="id"
          dataSource={snapshot.tasks}
          loading={loading}
          scroll={{ x: 900 }}
          columns={[
            { title: "任务 ID", dataIndex: "id", width: 150 },
            { title: "操作类型", dataIndex: "type", width: 180 },
            { title: "目标", dataIndex: "target", width: 180, render: (value: string | undefined) => value || "—" },
            { title: "创建时间", dataIndex: "createdAt", width: 180 },
            { title: "状态", dataIndex: "status", width: 120, render: (value: TaskItem["status"]) => <StatusTag status={value} /> },
            { title: "任务消息", dataIndex: "message", minWidth: 220 },
            { title: "结果", dataIndex: "result", minWidth: 220, render: (value: string | undefined, task: TaskItem) => value ?? (task.status === "succeeded" || task.status === "failed" ? "—" : "未完成") },
          ]}
          locale={{ emptyText: "暂无任务" }}
        />
      </Card>
    </Space>
  );
}

function HealthModulePanel({ module }: { module: HealthModule }) {
  const title = HEALTH_MODULE_TITLES[module.id];
  const statusText = module.status === "healthy" ? "在线" : module.status === "warning" ? "注意" : "离线";

  return (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <Typography.Text strong>{title}</Typography.Text>
        <StatusTag status={module.status} />
      </div>
      <div className="mb-2"><Typography.Text strong className="text-lg">{statusText}</Typography.Text></div>
      <Space orientation="vertical" size={4}>
        <Typography.Text>{module.description}</Typography.Text>
        <Typography.Text>延迟：{module.latency === null ? "未知" : `${module.latency}ms`}</Typography.Text>
      </Space>
    </div>
  );
}
