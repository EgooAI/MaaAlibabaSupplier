import type { OutboxStatus } from "@/domain/contract.generated";
import type { OutboxTask } from "@/types/chatOperations";

export const outboxLabels: Record<OutboxStatus, string> = {
  queued: "等待搜索", navigating: "正在搜索联系人", awaiting_confirmation: "等待截图确认",
  queued_send: "已确认联系人，等待执行", running: "正在操作客户端", verifying: "正在核对本地消息",
  observed: "本地发现匹配消息", filled: "已填入（未发送）", failed: "任务失败", unknown: "结果未知", cancelled: "已取消",
};
const TERMINAL: OutboxStatus[] = ["observed", "filled", "failed", "unknown", "cancelled"];
const CANCELLABLE: OutboxStatus[] = ["queued", "awaiting_confirmation", "queued_send"];
export const isTerminal = (task: OutboxTask) => TERMINAL.includes(task.status);
export const canCancel = (task: OutboxTask) => CANCELLABLE.includes(task.status);
export const canRetry = (task: OutboxTask) => task.status === "failed" && !task.may_have_sent;

export function screenshotTtlSeconds(
  task: Pick<OutboxTask, "screenshot_at" | "screenshot_expires_at">,
): number | null {
  if (task.screenshot_at === null || task.screenshot_expires_at == null) return null;
  return Math.max(0, Math.round(task.screenshot_expires_at - task.screenshot_at));
}

export const frameFresh = (task: OutboxTask, now = Date.now()) =>
  task.status === "awaiting_confirmation"
  && Boolean(task.screenshot_id)
  && task.screenshot_at !== null
  && task.screenshot_expires_at != null
  && now >= task.screenshot_at * 1000
  && now < task.screenshot_expires_at * 1000;
