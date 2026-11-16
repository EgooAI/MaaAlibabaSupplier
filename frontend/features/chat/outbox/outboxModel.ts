import type { OutboxStatus } from "@/domain/contract.generated";
import type { OutboxTask } from "@/types/chatOperations";
import type { ChatMessage } from "@/types/chatCanonical";

export const outboxLabels: Record<OutboxStatus, string> = {
  queued: "正在排队中", navigating: "正在准备执行",
  running: "正在执行中", verifying: "正在核验执行结果",
  observed: "正在等待数据同步", filled: "已填入", failed: "任务失败", unknown: "任务可能失败", cancelled: "已取消",
};
const TERMINAL: OutboxStatus[] = ["observed", "filled", "failed", "unknown", "cancelled"];
export const isTerminal = (task: OutboxTask) => TERMINAL.includes(task.status);
export const canCancel = (task: OutboxTask) => task.status === "queued";
export const canRetry = (task: OutboxTask) => task.status === "failed" && !task.may_have_sent && task.content_format !== "html";

export function pendingBubbles(tasks: OutboxTask[], messages: ChatMessage[], seller: string, sid: string) {
  const synced = new Set(messages.map((message) => message.externalMid));
  return tasks.filter((task) => String(task.conversation_id) === sid && task.action === "send"
    && task.status !== "cancelled"
    && !(task.status === "observed" && task.matched_message_id
      && synced.has(`alibaba_icbu:${seller}:${task.matched_message_id}`)));
}
