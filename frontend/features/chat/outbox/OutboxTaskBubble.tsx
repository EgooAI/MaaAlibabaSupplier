"use client";

import { LoadingOutlined, MoreOutlined, ShopOutlined } from "@ant-design/icons";
import { Avatar, Button, Popover } from "antd";
import { TimeText } from "@/components/TimeText";
import { useAccount } from "@/features/account/AccountProvider";
import type { OutboxTask } from "@/types/chatOperations";
import type { useOutbox } from "./useOutbox";
import { canCancel, canRetry, isTerminal, outboxLabels } from "./outboxModel";

type Props = { task: OutboxTask; outbox: Pick<ReturnType<typeof useOutbox>, "busy" | "mutate"> };

const reasons: Record<string, string> = {
  message_not_observed: "尚未在本地消息中找到匹配记录。",
  multiple_candidate_messages: "存在多条相同消息，无法确定本次发送对应哪一条。",
  ambiguous_send_attempts: "相同内容的发送任务存在歧义，请核对实际消息。",
  message_already_claimed: "匹配消息已经关联到其他发送任务。",
  baseline_invalid: "缺少有效的发送前消息基线。",
  snapshot_invalid: "本地消息数据暂不可用。",
  snapshot_scope_or_time_mismatch: "消息来源或时间与发送记录不一致。",
  local_message_observed: "本地已发现匹配消息，等待会话同步。",
  restart_before_execution: "程序重启，本任务未继续执行。",
  restart_confirmation_lost: "旧版确认任务已失效，可以重新提交执行。",
  restart_execution_uncertain: "程序在执行期间重启，请核对实际结果。",
  shutdown_before_execution: "程序停止，本任务未执行。",
  shutdown_execution_uncertain: "程序在执行期间停止，请核对实际结果。",
  outbox_operation_failed: "客户端操作未完成，详细原因见后端诊断记录。",
};

export function OutboxTaskStatus({ task, outbox }: Props) {
  const { snapshot, blocked } = useAccount();
  const canOperate = !blocked && Boolean(snapshot?.capabilities.operate_client);
  const exceptional = task.status === "failed" || task.status === "unknown";
  const label = task.status === "observed" ? "本地已匹配，等待同步"
    : task.status === "unknown" ? "结果未知，请核对消息"
    : task.action === "test" && task.status === "queued" ? "等待填入" : outboxLabels[task.status];
  return <div className={`flex flex-wrap items-center justify-end gap-1 text-xs ${exceptional ? "text-amber-700" : "text-slate-400"}`}>
    {!isTerminal(task) ? <LoadingOutlined aria-hidden /> : null}
    <span role="status">{label}</span>
    {canRetry(task) ? <Button type="text" size="small" disabled={!canOperate} loading={outbox.busy} onClick={() => void outbox.mutate(task, "retry")}>重试</Button> : null}
    <Popover trigger="click" placement="topRight" content={<div className="max-w-80 space-y-2 text-xs">
      <div>{task.login_id} · 第 {task.attempt} 次</div>
      <TimeText value={task.created_at} />
      {task.action === "test" ? <div className="max-h-40 overflow-auto whitespace-pre-wrap break-words">{task.content}</div> : null}
      {task.reason ? <p>{reasons[task.reason] ?? task.reason}</p> : null}
      {task.status === "unknown" || task.may_have_sent ? <p>请以实际消息核对结果，任务不会自动重发。</p> : null}
      {task.status === "observed" ? <p>本地匹配不代表平台送达或对方已读。</p> : null}
      {canCancel(task) ? <Button size="small" disabled={blocked} loading={outbox.busy} onClick={() => void outbox.mutate(task, "cancel")}>取消任务</Button> : null}
    </div>}>
      <Button type="text" size="small" aria-label="发送任务详情" icon={<MoreOutlined />} />
    </Popover>
  </div>;
}

export function OutboxTaskBubble({ task, outbox }: Props) {
  return <article aria-label={`发送任务 ${task.id}`} className="flex justify-end">
    <div className="flex min-w-0 max-w-full flex-row-reverse items-start gap-2 sm:max-w-[88%] sm:gap-3">
      <div className="hidden shrink-0 sm:block"><Avatar icon={<ShopOutlined />} style={{ backgroundColor: "#1677ff" }} /></div>
      <div className="min-w-0">
        <div className="mb-1.5 text-right text-[11px] text-slate-400"><TimeText value={task.created_at} /></div>
        <div className="whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-blue-50 px-3 py-2.5 text-sm leading-relaxed [overflow-wrap:anywhere] sm:px-4">{task.content}</div>
        <div className="mt-1"><OutboxTaskStatus task={task} outbox={outbox} /></div>
      </div>
    </div>
  </article>;
}
