"use client";

import { Alert, Card, Space, Tabs, Tag, Typography } from "antd";
import { AppTable } from "@/components/AppTable";
import { experimentStatus, observationTime, taskStatus, workStatus } from "@/domain/status/backgroundModel";
import { outboxLabels } from "@/features/chat/outbox/outboxModel";
import type { OutboxTask } from "@/types/chatOperations";
import type { BackgroundOutboxItem, SystemStatusSnapshot } from "@/types/status";
import { WorkStateTag } from "./WorkerStatus";

const outboxStatusColors: Record<string, string> = {
  failed: "red", unknown: "orange", awaiting_confirmation: "orange",
  running: "blue", navigating: "blue", verifying: "blue",
};
const outboxEvidenceLabels: Record<string, string> = {
  observed: "本地匹配，平台送达待确认",
  filled: "仅填入，未发送",
};
const queueLabels: Record<string, string> = { maafw: "GUI", translation: "翻译" };

function OutboxStatus({ status }: { status: string }) {
  return <Tag color={outboxStatusColors[status] ?? "default"}>{outboxLabels[status as OutboxTask["status"]] ?? `未知状态：${status}`}</Tag>;
}

function OutboxEvidence({ item }: { item: BackgroundOutboxItem }) {
  return <Space orientation="vertical" size={0}>
    <Typography.Text>{outboxEvidenceLabels[item.status] ?? "暂无平台送达证据"}</Typography.Text>
    {item.mayHaveSent ? <Typography.Text type="warning">可能已发送，勿直接重发</Typography.Text> : null}
  </Space>;
}

export function BackgroundTasks({ snapshot }: { snapshot: SystemStatusSnapshot }) {
  const background = snapshot.background;
  const outboxError = background?.errors.outbox;
  const tasks = background ? background.queueTasks : snapshot.tasks.map((task) => ({ ...task, queue: "legacy" }));
  const empty = (provided: boolean, label: string) => provided ? label : "当前快照未提供此数据";
  return (
    <Card title="近期业务任务">
      <Tabs items={[
        {
          key: "queues", label: "GUI / 翻译任务",
          children: <Space orientation="vertical" size="middle" className="w-full">
            {background ? <Typography.Text type="secondary">仅含当前账号代次、保留期内的任务，活动任务优先，每类队列最多 100 条。{background.queueCounts ? "卡片计数覆盖保留期内全部任务，不受列表上限影响。" : "全量计数未提供，卡片仅统计列表样本。"}</Typography.Text> : null}
            <Typography.Text type="secondary">任务完成表示该操作结束。GUI 发送与核查结果请查看 Outbox。</Typography.Text>
            {!background ? <Typography.Text type="secondary">仅显示旧版快照任务，统一队列数据未提供。</Typography.Text> : null}
            <AppTable rowKey={(task) => `${task.queue}:${task.id}`} dataSource={tasks} scroll={{ x: 1100 }} columns={[
              { title: "队列", width: 110, render: (_, task) => queueLabels[task.queue] ?? "旧版任务" },
              { title: "任务 / ID", width: 210, render: (_, task) => <>{task.type}<br /><Typography.Text type="secondary">{task.id}</Typography.Text></> },
              { title: "状态", width: 100, render: (_, task) => <WorkStateTag status={taskStatus(task)} /> },
              { title: "目标", dataIndex: "target", width: 150, render: (value?: string) => value || "—" },
              { title: "创建 / 耗时", width: 180, render: (_, task) => <>{task.createdAt}<br />{task.duration}</> },
              { title: "任务消息", dataIndex: "message", width: 200 },
              { title: "结果", render: (_, task) => task.result ?? (task.status === "succeeded" || task.status === "failed" ? "无结果记录" : "未完成") },
            ]} locale={{ emptyText: empty(Boolean(background), "暂无近期队列任务") }} />
          </Space>,
        },
        {
          key: "experiments", label: "Agent 实验",
          children: <Space orientation="vertical" size="middle" className="w-full">
            <Typography.Text type="secondary">当前工作区最多显示 50 条运行记录，活动任务优先。</Typography.Text>
            <AppTable rowKey="runId" dataSource={background?.experiments ?? []} scroll={{ x: 1000 }} columns={[
              { title: "实验 / 运行 ID", width: 240, render: (_, run) => <>{run.experimentId}<br /><Typography.Text type="secondary">{run.runId}</Typography.Text></> },
              { title: "步骤", width: 80, render: (_, run) => run.stepIndex + 1 },
              { title: "状态", width: 230, render: (_, run) => <WorkStateTag status={experimentStatus(run)} /> },
              { title: "开始 / 结束", width: 180, render: (_, run) => <>{observationTime(run.startedAt)}<br />{observationTime(run.finishedAt)}</> },
              { title: "变体结果", render: (_, run) => <Space orientation="vertical" size={0}>{run.variants.map((variant) => <div key={variant.variantId}>{variant.variantId} <WorkStateTag status={experimentStatus({ ...run, status: variant.status, cancelRequested: false })} />{variant.error ? <Typography.Text type="danger">{variant.error}</Typography.Text> : null}</div>)}</Space> },
            ]} locale={{ emptyText: empty(Boolean(background), "当前工作区暂无近期实验") }} />
          </Space>,
        },
        {
          key: "outbox", label: "Outbox",
          children: <Space orientation="vertical" size="middle" className="w-full">
            <Typography.Text type="secondary">状态计数覆盖当前卖家与数据目录的全部 Outbox 记录，列表仅显示最近 20 条。</Typography.Text>
            <Typography.Text type="secondary">发送与填入请在聊天工作台确认联系人、内容和截图。GUI 完成、本地匹配与平台送达是不同证据。</Typography.Text>
            {outboxError ? <Alert showIcon type="warning" title={`Outbox 读取失败：${outboxError}`} /> : background ? <Space wrap>{Object.entries(background.outbox.counts).map(([status, count]) => <span key={status}><OutboxStatus status={status} />{count}</span>)}</Space> : <WorkStateTag status={workStatus("unknown")} />}
            <AppTable rowKey="id" dataSource={background?.outbox.recent ?? []} scroll={{ x: 1050 }} columns={[
              { title: "任务 ID", dataIndex: "id", width: 200 },
              { title: "操作 / 会话", width: 140, render: (_, item) => <>{item.action === "test" ? "仅填入" : "发送"} · {item.conversationId}</> },
              { title: "业务状态", width: 210, render: (_, item) => <OutboxStatus status={item.status} /> },
              { title: "创建 / 更新", width: 180, render: (_, item) => <>{observationTime(item.createdAt)}<br />{observationTime(item.updatedAt)}</> },
              { title: "发送证据", render: (_, item) => <OutboxEvidence item={item} /> },
            ]} locale={{ emptyText: outboxError ? "读取失败，任务数量未知" : empty(Boolean(background), "当前工作区暂无 Outbox 任务") }} />
          </Space>,
        },
      ]} />
    </Card>
  );
}
