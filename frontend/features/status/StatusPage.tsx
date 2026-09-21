"use client";

import { ExperimentOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Empty, Row, Space, Typography } from "antd";
import { AppTable } from "@/components/AppTable";
import { EMPTY_TEXT } from "@/components/empty";
import { StatusTag } from "@/components/StatusTag";
import { formatDateTime } from "@/domain/time";
import type { SourceSyncStatus } from "@/types/connection";
import type { HealthModule, TaskItem } from "@/types/status";
import { useStatusWorkbench } from "./hooks/useStatusWorkbench";
import { useAccount } from "@/features/account/AccountProvider";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { SyncStatus } from "@/features/account/SyncStatus";
import { WorkerStatus } from "./WorkerStatus";

export function StatusPage() {
  const account = useAccount();
  const canDiagnose = !account.blocked && Boolean(account.snapshot?.client.connected);
  const { snapshot, loading, lastFailure, creatingTask, testingNode, createTestTask, runNodeTest } = useStatusWorkbench();
  const failure = lastFailure ? <Alert type="warning" showIcon title={`状态观察失败（${new Date(lastFailure.at).toLocaleTimeString()}）`} description={`${lastFailure.message}。${snapshot ? `当前显示上次成功快照（${snapshot.updatedAt}），可能已过期。` : "尚无可用快照。"}`} /> : null;

  if (!snapshot) {
    return (
      <Space orientation="vertical" size="large" className="w-full">
        {failure}
        <Card loading={loading}>
          {!loading ? <Empty description={EMPTY_TEXT.status} /> : null}
        </Card>
      </Space>
    );
  }

  return (
    <Space orientation="vertical" size="large" className="w-full">
      {failure}
      <ChatSyncCard observedSource={snapshot.source} />
      {snapshot.workers ? <WorkerStatus workers={snapshot.workers} queues={snapshot.queues} /> : null}

      <Card title="模块状态" extra={<Typography.Text type="secondary">更新于 {snapshot.updatedAt}（观察完成后间隔 2 秒）</Typography.Text>} loading={loading}>
        <Row gutter={[16, 16]}>
          {snapshot.modules.map((module) => <Col xs={24} md={8} key={module.id}><HealthModulePanel module={module} /></Col>)}
        </Row>
        {snapshot.lastDiagnostic ? <Typography.Paragraph type="secondary" className="mt-3 mb-0">上次手动检查完成于 {formatDateTime(snapshot.lastDiagnostic.completed_at)} · 卖家 {snapshot.lastDiagnostic.context.self_ali_id || "未选择"} · {snapshot.lastDiagnostic.entry} · {snapshot.lastDiagnostic.currentContext ? "账号与窗口上下文匹配；不保证当前界面仍有效" : "账号或窗口上下文已失效"}</Typography.Paragraph> : null}
      </Card>

      <Card title="诊断动作">
        <DataDirBanner />
        <Space wrap>
          <Button type="primary" icon={<ExperimentOutlined />} loading={creatingTask} onClick={createTestTask}>创建测试任务</Button>
          <Button disabled={!canDiagnose} loading={testingNode === "ChatInput_GoToInput"} onClick={() => void runNodeTest("ChatInput_GoToInput")}>检查聊天输入框</Button>
          <Button disabled={!canDiagnose} loading={testingNode === "ContactSearch_GoToSearch"} onClick={() => void runNodeTest("ContactSearch_GoToSearch")}>检查联系人搜索框</Button>
        </Space>
        <Typography.Paragraph type="secondary" className="mt-3 mb-0">界面检查仅需接入客户端；会排队执行，仅识别控件，不点击或输入。选择卖家并接入客户端后可发送、填入测试和跳转联系人；发送与填入仍需查看截图并确认联系人与内容。</Typography.Paragraph>
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
          locale={{ emptyText: EMPTY_TEXT.tasks }}
        />
      </Card>
    </Space>
  );
}

function HealthModulePanel({ module }: { module: HealthModule }) {
  return (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <Typography.Text strong>{module.name}</Typography.Text>
        <StatusTag status={module.status} />
      </div>
      <Space orientation="vertical" size={2}>
        <Typography.Text>{module.description}</Typography.Text>
        {module.observedAt != null ? <Typography.Text type="secondary">观察时间：{formatDateTime(module.observedAt)}</Typography.Text> : null}
        <Typography.Text type="secondary">延迟：{module.latency === null ? "未知" : `${module.latency}ms`}</Typography.Text>
      </Space>
    </div>
  );
}

function SyncDetailTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-3">
      <Typography.Text strong className="block">{label}</Typography.Text>
      <Typography.Text className="break-all">{value}</Typography.Text>
    </div>
  );
}

function ChatSyncCard({ observedSource }: { observedSource?: SourceSyncStatus | null }) {
  const { snapshot, blocked } = useAccount();
  const source: SourceSyncStatus | null = blocked ? null : observedSource ?? snapshot?.source ?? null;
  const time = (value: number | null) => (value == null ? "尚无记录" : formatDateTime(value));
  return (
    <Card title="聊天同步">
      <Space orientation="vertical" size="middle" className="w-full">
        <SyncStatus />
        {source ? (
          <>
            <Row gutter={[12, 12]}>
              <Col xs={12} md={8}><SyncDetailTile label="最近检查" value={time(source.last_checked)} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="最近成功观察源库" value={time(source.last_observed_at)} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="最近尝试" value={time(source.last_attempt)} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="计划重试" value={source.retry_at == null ? "无" : time(source.retry_at)} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="最近提交" value={`新增 ${source.counts.inserted} · 更新 ${source.counts.updated} · 未变 ${source.counts.unchanged}`} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="同步版本" value={`已提交 ${source.revision} · 数据源 ${source.source_revision} · 已应用 ${source.applied_source_revision}`} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="后台检查" value={`${source.auto_enabled ? "已启用" : "未启用"}${source.source_dirty ? " · 数据源有变化" : ""}${source.pending ? " · 等待同步" : ""}`} /></Col>
            </Row>
            {source.last_error ? <Alert type="error" showIcon title={source.last_error} /> : null}
            {source.observation_stale ? <Alert type="warning" showIcon title={`尚无成功源库观察或已超过 ${source.observation_max_age_s} 秒。已有存档可能过期；这不表示同步任务已失败。`} /> : null}
          </>
        ) : null}
      </Space>
    </Card>
  );
}
