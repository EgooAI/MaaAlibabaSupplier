"use client";

import { Alert, Card, Space, Typography } from "antd";
import type { QueueObservation, WorkerObservation } from "@/types/status";
import { formatDateTime } from "@/domain/time";

const names: Record<string, string> = { "im-source-check": "源库检查服务", "outbox-verifier": "发送结果核对服务" };
const phases: Record<string, string> = {
  not_started: "未启动", starting: "启动中", checking_source: "检查源库", waiting: "等待下一轮",
  reconciling: "核对中", scanning: "读取待核对任务", reading_source: "读取本地消息", stopped: "循环已退出",
};
const time = (value: number | null | undefined) => value == null ? "尚无记录" : formatDateTime(value);

export function WorkerStatus({ workers, queues }: { workers: Record<string, WorkerObservation | null>; queues?: Record<string, QueueObservation> }) {
  return (
    <Card title="后台观察">
      <Space orientation="vertical" className="w-full" size="middle">
        <Typography.Text type="secondary">线程存活和循环完成不代表源库新鲜、CRM 已提交或消息已送达。阶段耗时仅供观察，不会据此自动判定失败或重发。</Typography.Text>
        {Object.entries(workers).map(([id, worker]) => (
          <div key={id}>
            <Typography.Text strong>{names[id] ?? id}：{!worker?.started ? "未启动" : worker.alive ? (worker.stopping ? "停止中，线程仍存活" : "线程存活") : "线程未存活"}</Typography.Text>
            {worker ? <Space orientation="vertical" size={0} className="block">
              <Typography.Text>阶段：{phases[worker.phase] ?? worker.phase} · 持续 {worker.phase_age_s == null ? "未知" : `${Math.floor(worker.phase_age_s)} 秒`} · 已完成循环 {worker.completed_iterations}</Typography.Text>
              <Typography.Text type="secondary">启动：{time(worker.started_at)} · 最近心跳：{time(worker.heartbeat_at)} · 最近进展：{time(worker.last_progress_at)}</Typography.Text>
              <Typography.Text type="secondary">{id === "im-source-check" ? "待提交同步目标" : "待核对数量"}：{worker.pending == null ? "未观察 / 不适用" : `${worker.pending}（观察于 ${time(worker.pending_observed_at)}）`}{worker.context ? ` · 卖家 ${worker.context.self_ali_id || "未选择"}` : ""}</Typography.Text>
              {worker.last_error ? <Alert type="warning" showIcon title={`上次循环异常：${worker.last_error}`} /> : null}
            </Space> : null}
          </div>
        ))}
        {Object.entries(queues ?? {}).map(([id, queue]) => (
          <div key={id}>
            <Typography.Text strong>{id === "maafw" ? "界面任务队列" : id === "translation" ? "翻译任务队列" : id}：{!queue.initialized ? "未启动" : queue.alive ? "线程存活" : "线程未存活"}</Typography.Text>
            <Typography.Paragraph className="mb-0">排队 {queue.pending} · 当前任务：{queue.current_age_s == null ? "无" : `已运行 ${Math.floor(queue.current_age_s)} 秒`} · 最近完成：{time(queue.last_completed)}</Typography.Paragraph>
          </div>
        ))}
      </Space>
    </Card>
  );
}
