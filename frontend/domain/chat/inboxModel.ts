import type { ConversationQuery, ReplyState } from "@/types/inbox";

export const replyStates: ReplyState[] = ["needs_reply", "waiting_customer", "history_pending", "none", "unknown"];

export function inboxQueryFromUrl(params: URLSearchParams): ConversationQuery {
  const state = params.get("reply_state");
  const scope = params.get("search_scope");
  return {
    q: params.get("q") || undefined,
    search_scope: scope === "customer" || scope === "messages" ? scope : "all",
    country: params.get("country") || undefined,
    tag: params.get("tag") || undefined,
    reply_state: replyStates.includes(state as ReplyState) ? state as ReplyState : undefined,
    unread: params.has("unread") ? params.get("unread") === "true" : undefined,
    overdue: params.has("overdue") ? params.get("overdue") === "true" : undefined,
  };
}

export const inboxCards = [
  { key: "needs_reply", label: "待回复", query: "reply_state=needs_reply" },
  { key: "overdue", label: "超时", query: "reply_state=needs_reply&overdue=true" },
  { key: "unread", label: "本工作台未读", query: "unread=true" },
  { key: "history_pending", label: "历史待确认", query: "reply_state=history_pending" },
  { key: "waiting_customer", label: "等待客户", query: "reply_state=waiting_customer" },
] as const;
