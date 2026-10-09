"use client";

import { Alert, Card, Col, Row, Space, Tag, Tooltip, Typography } from "antd";
import type { QueueObservation, WorkerObservation } from "@/types/status";
import { formatDateTime } from "@/domain/time";

const workerNames: Record<string, string> = {
  "im-source-check": "源库检查服务",
  "outbox-verifier": "发送结果核对服务",
  "card-sweep": "卡片清扫服务",
};
const phases: Record<string, string> = {
  not_started: "未启动", starting: "启动中", checking_source: "检查源库", waiting: "等待下一轮",
  reconciling: "核对中", scanning: "读取待核对任务", reading_source: "读取本地消息", stopped: "循环已退出",
  selecting: "筛选目标", sweeping: "导航会话", idle: "空闲（等待手动触发）",
};
const queueNames: Record<string, string> = { maafw: "界面任务队列", translation: "翻译任务队列" };
const time = (value: number | null | undefined) => value == null ? "尚无记录" : formatDateTime(value);
const seconds = (value: number | null | undefined) => value == null ? "未知" : `${Math.floor(value)} 秒`;

function workerState(worker: WorkerObservation | null) {
  if (!worker?.started) return { label: "未启动", color: "default" };
  if (!worker.alive) return { label: "线程未存活", color: "red" };
  return worker.stopping ? { label: "停止中，线程仍存活", color: "orange" } : { label: "线程存活", color: "green" };
}

function queueState(queue: QueueObservation) {
  if (!queue.initialized) return { label: "未启动", color: "default" };
  return queue.alive ? { label: "线程存活", color: "green" } : { label: "线程未存活", color: "red" };
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: "success" | "warning" }) {
  const toneClass = tone === "success" ? "text-emerald-600" : tone === "warning" ? "text-amber-600" : "text-slate-800";
  return (
    <div className="rounded-md bg-slate-50 px-3 py-1.5">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${toneClass}`}>{value}</div>
    </div>
  );
}

function SweepMetrics({ worker, pools }: { worker: WorkerObservation; pools?: Record<string, number> }) {
  const enriched = worker.enriched ?? 0;
  const failed = worker.failed ?? 0;
  return (
    <div className="flex flex-wrap gap-2">
      <Metric label="目标会话" value={worker.targets ?? 0} />
      <Metric label="已访问" value={worker.visited ?? 0} />
      <Metric label="已富化" value={enriched} tone={enriched > 0 ? "success" : undefined} />
      <Metric label="未富化" value={failed} tone={failed > 0 ? "warning" : undefined} />
      <Metric label="最近一轮" value={time(worker.last_sweep_at)} />
      {pools?.product_cards != null ? <Metric label="产品卡池" value={pools.product_cards} /> : null}
    </div>
  );
}

function WorkerPanel({ id, worker, pools }: { id: string; worker: WorkerObservation | null; pools?: Record<string, number> }) {
  const state = workerState(worker);
  const tag = <Tag color={state.color}>{state.label}</Tag>;
  return (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <Typography.Text strong>{workerNames[id] ?? id}</Typography.Text>
        {worker ? (
          <Tooltip title={<div className="whitespace-pre-line">{`启动：${time(worker.started_at)}\n最近心跳：${time(worker.heartbeat_at)}\n最近进展：${time(worker.last_progress_at)}`}</div>}>
            <span className="cursor-help">{tag}</span>
          </Tooltip>
        ) : tag}
      </div>
      {worker ? (
        <Space orientation="vertical" size={2} className="w-full">
          <Typography.Text>
            阶段：{phases[worker.phase] ?? worker.phase} · 持续 {seconds(worker.phase_age_s)} · 已完成循环 {worker.completed_iterations}
          </Typography.Text>
          {id === "card-sweep" ? <SweepMetrics worker={worker} pools={pools} /> : (
            <Tooltip title={`观察于 ${time(worker.pending_observed_at)}`}>
              <Typography.Text type="secondary" className="cursor-help">
                {id === "im-source-check" ? "待提交同步目标" : "待核对数量"}：{worker.pending == null ? "未观察 / 不适用" : worker.pending}
              </Typography.Text>
            </Tooltip>
          )}
          {worker.last_error ? <Alert type="warning" showIcon title={`上次循环异常：${worker.last_error}`} /> : null}
        </Space>
      ) : <Typography.Text type="secondary">尚无观察记录。</Typography.Text>}
    </div>
  );
}

function QueuePanel({ id, queue }: { id: string; queue: QueueObservation }) {
  const state = queueState(queue);
  return (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <Typography.Text strong>{queueNames[id] ?? id}</Typography.Text>
        <Tooltip title={`最近完成：${time(queue.last_completed)}`}>
          <span className="cursor-help"><Tag color={state.color}>{state.label}</Tag></span>
        </Tooltip>
      </div>
      <Typography.Text>排队 {queue.pending} · 当前任务：{queue.current_age_s == null ? "无" : `已运行 ${Math.floor(queue.current_age_s)} 秒`}</Typography.Text>
    </div>
  );
}

export function WorkerStatus({ workers, queues, pools }: {
  workers: Record<string, WorkerObservation | null>;
  queues?: Record<string, QueueObservation>;
  pools?: Record<string, number>;
}) {
  return (
    <Card title="后台观察">
      <Typography.Text type="secondary">线程存活和循环完成不代表源库新鲜、CRM 已提交或消息已送达。阶段耗时仅供观察，不会据此自动判定失败或重发。</Typography.Text>
      <Row gutter={[16, 16]} className="mt-4">
        {Object.entries(workers).map(([id, worker]) => (
          <Col xs={24} xl={12} key={id}><WorkerPanel id={id} worker={worker} pools={pools} /></Col>
        ))}
        {Object.entries(queues ?? {}).map(([id, queue]) => (
          <Col xs={24} xl={12} key={`queue-${id}`}><QueuePanel id={id} queue={queue} /></Col>
        ))}
      </Row>
    </Card>
  );
}
