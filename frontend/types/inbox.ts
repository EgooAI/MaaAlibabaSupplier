import type { Conversation } from "./chatCanonical";
import type { LostReason, PortraitStage } from "./portrait";

export type ReplyState = "needs_reply" | "waiting_customer" | "history_pending" | "none" | "unknown";

export interface InboxStateDto {
  unread_count: number;
  reply_state: ReplyState;
  pending_since: number | null;
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
  portrait_stage?: PortraitStage[];
  portrait_completeness?: "all" | "maintained" | "unmaintained";
  business_tag?: string[];
  lost_reason?: LostReason;
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
  portrait_revision?: number;
  tag_revision?: number;
}

export interface ReadReceipt {
  state: InboxStateDto & { read_seq: number; snapshot_seq: number };
  inbox_revision: number;
}

export interface InboxOverview {
  counts: Record<"total" | "unread" | "needs_reply" | "history_pending" | "waiting_customer", number>;
  inbox_revision: number;
  updated_at: number;
}
