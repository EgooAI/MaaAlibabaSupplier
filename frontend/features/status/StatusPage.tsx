"use client";

import { ExperimentOutlined, InfoCircleOutlined, ReloadOutlined, ScanOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, Collapse, Empty, Row, Space, Tabs, Tooltip, Typography } from "antd";
import { EMPTY_TEXT } from "@/components/empty";
import { StatusTag } from "@/components/StatusTag";
import { formatDateTime } from "@/domain/time";
import type { SourceSyncStatus } from "@/types/connection";
import type { HealthModule } from "@/types/status";
import { useStatusWorkbench } from "./hooks/useStatusWorkbench";
import { useAccount } from "@/features/account/AccountProvider";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { SyncStatus } from "@/features/account/SyncStatus";
import { WorkerStatus } from "./WorkerStatus";
import { BackgroundTasks } from "./BackgroundTasks";

export function StatusPage() {
  const account = useAccount();
  const canDiagnose = !account.blocked && Boolean(account.snapshot?.client.connected);
  const { snapshot, loading, refreshing, refresh, lastFailure, creatingTask, testingNode, sweepingCards, createTestTask, runNodeTest, sweepCards } = useStatusWorkbench();
  const failure = lastFailure ? <Alert type="warning" showIcon title={`状态观察失败（${new Date(lastFailure.at).toLocaleTimeString()}）`} description={`${lastFailure.message}。${snapshot ? `当前显示上次成功快照（${snapshot.updatedAt}），可能已过期。` : "尚无可用快照。"}`} /> : null;
  const refreshButton = <Button icon={<ReloadOutlined />} loading={refreshing} onClick={() => void refresh()}>刷新状态</Button>;

  if (!snapshot) {
    return (
      <Space orientation="vertical" size="large" className="w-full">
        {failure}
        <Card title="系统状态" extra={refreshButton} loading={loading}>
          {!loading ? <Empty description={EMPTY_TEXT.status} /> : null}
        </Card>
      </Space>
    );
  }

  return (
    <Space orientation="vertical" size="large" className="w-full">
      {failure}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Space orientation="vertical" size={0}>
          <Typography.Title level={4} className="m-0">系统状态</Typography.Title>
          <Tooltip title="页面可见时，每轮观察完成后约 2 秒自动刷新"><Typography.Text type="secondary">快照更新于 {snapshot.updatedAt}{lastFailure ? " · 可能已过期" : ""}</Typography.Text></Tooltip>
        </Space>
        {refreshButton}
      </div>
      <Tabs items={[
        { key: "background", label: "后台任务", children: <Space orientation="vertical" size="large" className="w-full">
          <WorkerStatus snapshot={snapshot} />
          <Collapse items={[{ key: "sync", label: "聊天同步详情与手动刷新", children: <ChatSyncCard observedSource={snapshot.source} /> }]} />
          <BackgroundTasks snapshot={snapshot} />
        </Space> },
        { key: "diagnostics", label: "连接与诊断", children: <Space orientation="vertical" size="large" className="w-full">
      <Card title="连接观察与最近诊断">
        <Row gutter={[16, 16]}>
          {snapshot.modules.map((module) => <Col xs={24} md={8} key={module.id}><HealthModulePanel module={module} /></Col>)}
        </Row>
        {snapshot.lastDiagnostic ? <Typography.Paragraph type="secondary" className="mt-3 mb-0">上次手动检查完成于 {formatDateTime(snapshot.lastDiagnostic.completed_at)} · 卖家 {snapshot.lastDiagnostic.context.self_ali_id || "未选择"} · {snapshot.lastDiagnostic.entry} · {snapshot.lastDiagnostic.currentContext ? "账号与窗口上下文匹配" : "账号或窗口上下文已失效"}</Typography.Paragraph> : null}
      </Card>

      <Card title="诊断动作">
        <DataDirBanner />
        <Space wrap>
          <Button type="primary" icon={<ExperimentOutlined />} disabled={account.blocked} loading={creatingTask} onClick={createTestTask}>创建测试任务</Button>
          <Button disabled={!canDiagnose} loading={testingNode === "ChatInput_GoToInput"} onClick={() => void runNodeTest("ChatInput_GoToInput")}>检查聊天输入框</Button>
          <Button disabled={!canDiagnose} loading={testingNode === "ContactSearch_GoToSearch"} onClick={() => void runNodeTest("ContactSearch_GoToSearch")}>检查联系人搜索框</Button>
          <Button disabled={!canDiagnose} loading={sweepingCards} icon={<ScanOutlined />} onClick={() => void sweepCards()}>立即扫描</Button>
        </Space>
        <Typography.Paragraph type="secondary" className="mt-3 mb-0">界面检查仅需客户端窗口已接入，会排队执行，只读取控件信息。选择卖家且客户端窗口接入后，可以发送、仅填入和跳转联系人。聊天页点击发送后会自动搜索联系人、输入并发送，结果在消息气泡下展示。“立即扫描”会导航到未富化产品卡所在的会话，促使客户端请求卡片详情。客户端只在收到新卡片消息时才会请求，历史卡片可能保持未富化状态。</Typography.Paragraph>
      </Card>

        </Space> },
      ]} />
    </Space>
  );
}

function HealthModulePanel({ module }: { module: HealthModule }) {
  const details = `${module.observedAt != null ? `观察时间：${formatDateTime(module.observedAt)} · ` : ""}延迟：${module.latency === null ? "未知" : `${module.latency}ms`}`;
  return (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <Typography.Text strong>{module.name}</Typography.Text>
        <Tooltip title={details}><span className="cursor-help"><StatusTag status={module.status} /></span></Tooltip>
      </div>
      <Typography.Text>{module.description}</Typography.Text>
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
  const { blocked } = useAccount();
  const source = blocked ? null : observedSource;
  const time = (value: number | null) => (value == null ? "尚无记录" : formatDateTime(value));
  return (
      <Space orientation="vertical" size="middle" className="w-full">
        <SyncStatus />
        {source ? (
          <>
            <Row gutter={[12, 12]}>
              <Col xs={12} md={8}><SyncDetailTile label="最近成功观察源库" value={time(source.last_observed_at)} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="最近一轮提交" value={`新增 ${source.counts.inserted} · 更新 ${source.counts.updated} · 未变 ${source.counts.unchanged}`} /></Col>
              <Col xs={12} md={8}><SyncDetailTile label="后台同步" value={`${source.auto_enabled ? "已启用" : "未启用"}${source.source_dirty ? " · 数据源有变化" : ""}${source.pending ? " · 等待同步" : ""}`} /></Col>
            </Row>
            <Tooltip title={<div className="whitespace-pre-line">{`最近检查：${time(source.last_checked)}\n最近尝试：${time(source.last_attempt)}\n计划重试：${source.retry_at == null ? "无" : time(source.retry_at)}\n同步版本：已提交 ${source.revision} · 数据源 ${source.source_revision} · 已应用 ${source.applied_source_revision}`}</div>}>
              <InfoCircleOutlined tabIndex={0} aria-label="技术细节" className="cursor-help text-slate-400" />
            </Tooltip>
            {source.last_error ? <Alert type="error" showIcon title={source.last_error} /> : null}
            {source.observation_stale ? <Alert type="warning" showIcon title={`尚无成功源库观察或已超过 ${source.observation_max_age_s} 秒。已有存档可能过期。`} /> : null}
          </>
        ) : <Typography.Text type="secondary">当前快照未提供可用的聊天同步详情</Typography.Text>}
      </Space>
  );
}
