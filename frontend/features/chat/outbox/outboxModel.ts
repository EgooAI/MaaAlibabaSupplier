import type { OutboxStatus } from "@/domain/contract.generated";
import type { OutboxTask } from "@/types/chatOperations";
import type { ChatMessage } from "@/types/chatCanonical";

export const outboxLabels: Record<OutboxStatus, string> = {
  queued: "等待发送", navigating: "正在操作客户端",
  running: "正在操作客户端", verifying: "正在核对本地消息",
  observed: "本地发现匹配消息", filled: "已填入（未发送）", failed: "任务失败", unknown: "结果未知", cancelled: "已取消",
};
const TERMINAL: OutboxStatus[] = ["observed", "filled", "failed", "unknown", "cancelled"];
export const isTerminal = (task: OutboxTask) => TERMINAL.includes(task.status);
export const canCancel = (task: OutboxTask) => task.status === "queued";
export const canRetry = (task: OutboxTask) => task.status === "failed" && !task.may_have_sent;

export function pendingBubbles(tasks: OutboxTask[], messages: ChatMessage[], seller: string, sid: string) {
  const synced = new Set(messages.map((message) => message.externalMid));
  return tasks.filter((task) => String(task.conversation_id) === sid && task.action === "send"
    && task.status !== "cancelled"
    && !(task.status === "observed" && task.matched_message_id
      && synced.has(`alibaba_icbu:${seller}:${task.matched_message_id}`)));
}
