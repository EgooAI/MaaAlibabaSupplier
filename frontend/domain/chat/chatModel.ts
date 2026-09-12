import type { ChatMessage, Conversation, ConversationDetail } from "@/types/chatCanonical";
import type { MessageExecution } from "@/types/chatOperations";

export type ConversationGroupMode = "time" | "status" | "count";

export function sortConversations(conversations: Conversation[]) {
  return [...conversations].sort((a, b) => timestampOf(b.updatedAt) - timestampOf(a.updatedAt));
}

export function dialogueCountOf(conversation: Conversation) {
  return conversation.dialogueCount ?? 0;
}

const countGroupOrder = ["长会话：16条及以上", "中等长度会话：4-15条", "短会话：1-3条"];

export function dialogueCountGroup(conversation: Conversation) {
  const count = dialogueCountOf(conversation);
  if (count >= 16) return countGroupOrder[0];
  if (count >= 4) return countGroupOrder[1];
  return countGroupOrder[2];
}

export function groupConversations(conversations: Conversation[], mode: ConversationGroupMode, now = new Date()) {
  const groups = new Map<string, Conversation[]>();

  for (const conversation of conversations) {
    const key = mode === "status" ? statusLabel(conversation.status) : mode === "count" ? dialogueCountGroup(conversation) : dateGroup(conversation.updatedAt, now);
    const items = groups.get(key);
    if (items) {
      items.push(conversation);
    } else {
      groups.set(key, [conversation]);
    }
  }

  const entries = Array.from(groups.entries());
  if (mode === "count") {
    entries.sort(([a], [b]) => countGroupOrder.indexOf(a) - countGroupOrder.indexOf(b));
  }
  return entries.map(([label, items]) => ({ label, items }));
}

export function statusLabel(status: Conversation["status"]) {
  return {
    unread: "未读待回",
    following: "跟进中",
    waiting: "等待客户",
    closed: "已关闭",
  }[status];
}

export function stageLabel(stage: ConversationDetail["customer"]["stage"]) {
  return {
    unknown: "未知阶段",
    new: "新线索",
    interested: "高意向",
    negotiating: "谈判中",
    risk: "风险客户",
    done: "已成交",
  }[stage];
}

export function conversationTimeLabel(value: string) {
  const date = parseConversationDate(value);
  if (!date) return value;
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function mergeMessageTranslations(messages: ChatMessage[], translations: Array<{ messageId: string; translatedContent: string }>) {
  const translationMap = new Map(translations.map((item) => [item.messageId, item.translatedContent]));
  return messages.map((message) => {
    const translatedContent = translationMap.get(message.id);
    return translatedContent === undefined ? message : { ...message, translatedContent };
  });
}

export function mergeConversationTranslations<T extends ConversationDetail>(conversation: T, translations: Array<{ messageId: string; translatedContent: string }>): T {
  return { ...conversation, messages: mergeMessageTranslations(conversation.messages, translations) };
}

export function mergeConversationDetail(current: ConversationDetail, incoming: ConversationDetail): ConversationDetail {
  const translationMap = new Map(current.messages.flatMap((item) => item.translatedContent ? [[item.id, item.translatedContent] as const] : []));
  return {
    ...incoming,
    analysis: incoming.analysis ?? current.analysis,
    messages: incoming.messages.map((item) => {
      const translatedContent = translationMap.get(item.id);
      return translatedContent === undefined ? item : { ...item, translatedContent };
    }),
  };
}

export function messageExecutionState(execution: MessageExecution) {
  const status = execution.task_snapshot?.status;
  if (!execution.success || status === "failed") return "failed";
  if (status === "pending" || status === "running") return "pending";
  return "succeeded";
}

export function buildConversationExport(details: ConversationDetail[]) {
  const timestamp = Date.now();
  const content = details
    .map((conversation) => {
      const header = [
        `客户：${conversation.customer.name}`,
        `公司：${conversation.customer.company}`,
        `阶段：${stageLabel(conversation.customer.stage)}`,
      ].join("\n");
      const messages = conversation.messages
        .map((message) => `[${message.createdAt}] ${message.role}: ${message.content}${message.translatedContent ? `\n译文：${message.translatedContent}` : ""}`)
        .join("\n");
      return `${header}\n${messages}`;
    })
    .join("\n\n---\n\n");

  return {
    fileName: `conversation-export-${timestamp}.txt`,
    content,
  };
}

export function dateGroup(value: string, now = new Date()) {
  const date = parseConversationDate(value);
  if (!date) return "更早";
  const today = startOfLocalDay(now);
  const target = startOfLocalDay(date);
  const diffDays = Math.round((today.getTime() - target.getTime()) / 86_400_000);
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  return "更早";
}

function timestampOf(value: string) {
  return parseConversationDate(value)?.getTime() ?? 0;
}

function parseConversationDate(value: string) {
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const timestamp = Date.parse(normalized);
  return Number.isNaN(timestamp) ? null : new Date(timestamp);
}

function startOfLocalDay(value: Date) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}
