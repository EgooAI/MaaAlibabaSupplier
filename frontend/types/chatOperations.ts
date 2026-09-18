import type { SourceSyncStatus } from "./connection";

export type TranslationJobStatus = "pending" | "running" | "succeeded" | "failed";

export interface TranslationJobSnapshot {
  task_id: string;
  status: TranslationJobStatus;
  message: string;
}

export interface RequestTranslationsInput {
  texts: string[];
  force?: boolean;
  /** 提供会话 ID 时后端加载全量历史作为翻译上下文。 */
  conversationId?: number;
}

/** 提交翻译任务后立即返回的任务快照；空 task_id 表示无事可做。 */
export type RequestTranslationsResult = TranslationJobSnapshot;

export interface TranslationQueryInput {
  texts: string[];
}

/** 批量缓存查询结果：text -> 译文（null 表示尚未缓存）。 */
export interface TranslationQueryResult {
  translations: Record<string, string | null>;
}

export interface SendMessageInput {
  conversationId: string;
  content: string;
  action: "test" | "send";
  idempotency_key: string;
  draft_version?: number;
}

export interface OutboxTask {
  id: string;
  conversation_id: number;
  contact_ali_id: string;
  login_id: string;
  content: string;
  action: "send" | "test";
  status: "queued" | "navigating" | "awaiting_confirmation" | "queued_send" | "running" | "verifying" | "observed" | "filled" | "failed" | "unknown" | "cancelled";
  version: number;
  attempt: number;
  phase: string;
  may_have_sent: boolean;
  reason: string | null;
  created_at: number;
  updated_at: number;
  screenshot_id: string | null;
  screenshot_at: number | null;
  matched_message_id: string | null;
  idempotency_key: string;
  evidence?: unknown;
}

export interface SendMessageResult {
  outbox: OutboxTask;
}

export interface ExportConversationsInput {
  conversationIds: string[];
}
export interface ExportConversationsResult {
  file_name: string;
  content: string;
  archive_name?: string;
  missing?: string[];
  fileName?: string;
  archiveName?: string;
}

export interface ConversationRevision extends SourceSyncStatus {
  inbox_revision: number;
  next_due_at: number | null;
  // Archive availability, independent of source freshness and key validation.
  ready: boolean;
  reason?: string;
}
