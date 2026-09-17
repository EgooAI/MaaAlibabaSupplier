import type { OutboxTask } from "@/types/chatOperations";

export const outboxLabels: Record<OutboxTask["status"], string> = {
  queued: "等待搜索", navigating: "正在搜索联系人", awaiting_confirmation: "等待截图确认",
  queued_send: "已确认联系人，等待执行", running: "正在操作客户端", verifying: "正在核对本地消息",
  observed: "本地发现匹配消息", filled: "已填入（未发送）", failed: "任务失败", unknown: "结果未知", cancelled: "已取消",
};
export const isTerminal = (task: OutboxTask) => ["observed", "filled", "failed", "unknown", "cancelled"].includes(task.status);
export const canCancel = (task: OutboxTask) => ["queued", "awaiting_confirmation", "queued_send"].includes(task.status);
export const canRetry = (task: OutboxTask) => task.status === "failed" && !task.may_have_sent;
export const frameFresh = (task: OutboxTask, now = Date.now()) => task.status === "awaiting_confirmation" && Boolean(task.screenshot_id) && task.screenshot_at !== null && now >= task.screenshot_at * 1000 && now < task.screenshot_at * 1000 + 120_000;
