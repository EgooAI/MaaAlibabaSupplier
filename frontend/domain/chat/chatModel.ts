import type { ChatMessage, Conversation, ConversationDetail } from "@/types/chatCanonical";
import { formatMonthDay } from "@/domain/time";

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
    const key = mode === "status" ? statusLabel(conversation.replyState) : mode === "count" ? dialogueCountGroup(conversation) : dateGroup(conversation.updatedAt, now);
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

export function statusLabel(status: Conversation["replyState"] | (string & {})) {
  return ({
    needs_reply: "待回复",
    waiting_customer: "等待客户",
    history_pending: "历史待确认",
    none: "无需回复",
    unknown: "回复状态未知",
  } as Record<string, string>)[status] ?? String(status);
}

export function stageLabel(stage: ConversationDetail["customer"]["stage"] | (string & {})) {
  return ({
    unknown: "未知阶段",
    new: "新线索",
    interested: "高意向",
    negotiating: "谈判中",
    risk: "风险客户",
    done: "已成交",
  } as Record<string, string>)[stage] ?? String(stage);
}

export function conversationTimeLabel(value: string) {
  return formatMonthDay(value);
}

/** 可翻译消息：非卡片、不携带卡片、内容非空（文本气泡）。 */
export function isTranslatableMessage(message: ChatMessage) {
  return message.role !== "card" && !message.card && message.content.trim().length > 0;
}

/** 已解决 = 已缓存：非空译文，或空串 NO_NEED 哨兵（无需翻译）。null 表示尚未缓存。 */
export function isResolvedTranslation(value: string | null | undefined): value is string {
  return typeof value === "string";
}

/**
 * Merge text-keyed cache lookups into messages.
 *
 * Keyed by trimmed content, so stale responses for edited messages never apply;
 * null values are ignored instead of clearing existing translations. The empty
 * NO_NEED sentinel is resolved but carries no renderable translation, so it is
 * ignored here too (the caller tracks it via isResolvedTranslation).
 */
export function mergeMessageTextTranslations(messages: ChatMessage[], translations: Record<string, string | null>) {
  const byText = new Map(
    Object.entries(translations)
      .filter(([, value]) => typeof value === "string" && value.trim())
      .map(([text, value]) => [text, value as string]),
  );
  if (!byText.size) return messages;
  let changed = false;
  const merged = messages.map((message) => {
    const translatedContent = byText.get(message.content.trim());
    if (translatedContent === undefined || translatedContent === message.translatedContent) return message;
    changed = true;
    return { ...message, translatedContent };
  });
  return changed ? merged : messages;
}

export function mergeConversationDetail(current: ConversationDetail, incoming: ConversationDetail): ConversationDetail {
  return {
    ...incoming,
    analysis: current.analysis ?? incoming.analysis,
    messages: incoming.messages.map((item) => {
      const old = current.messages.find((candidate) => candidate.id === item.id);
      return old?.content === item.content && old.translatedContent ? { ...item, translatedContent: old.translatedContent } : item;
    }),
  };
}

export function buildConversationExport(details: ConversationDetail[], now = Date.now()) {
  const timestamp = now;
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
    file_name: `conversation-export-${timestamp}.txt`,
    content,
    fileName: `conversation-export-${timestamp}.txt`,
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
