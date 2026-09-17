import type { Conversation } from "./chatCanonical";

export type ReplyState = "needs_reply" | "waiting_customer" | "history_pending" | "none" | "unknown";

export interface InboxStateDto {
  unread_count: number;
  reply_state: ReplyState;
  pending_since: number | null;
  due_at: number | null;
  is_overdue: boolean;
  history_pending: boolean;
  uncertain: boolean;
}

export interface ConversationQuery {
  q?: string;
  search_scope?: "all" | "customer" | "messages";
  country?: string;
  tag?: string;
  reply_state?: ReplyState;
  unread?: boolean;
  overdue?: boolean;
  offset?: number;
  limit?: number;
  pagination_revision?: string;
}

export interface ConversationPage<T = Conversation> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
  inbox_revision: number;
  pagination_revision: string;
}

export interface ReadReceipt {
  state: InboxStateDto & { read_seq: number; snapshot_seq: number };
  inbox_revision: number;
}

export interface InboxSettings {
  timeout_seconds: number;
  inbox_revision: number;
}

export interface InboxOverview extends InboxSettings {
  counts: Record<"total" | "unread" | "needs_reply" | "overdue" | "history_pending" | "waiting_customer", number>;
  updated_at: number;
}
