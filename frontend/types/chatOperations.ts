import type { SourceSyncStatus } from "./connection";

export interface TranslateMessageInput {
  conversationId: string;
  messageId: string;
  targetLanguage: "zh-CN" | "en-US";
}

export interface TranslateMessageResult {
  messageId: string;
  translatedContent: string;
}

export interface RequestTranslationsInput {
  texts: string[];
  force?: boolean;
}

export interface RequestTranslationsResult {
  saved_count: number;
  translated_text: string | null;
  cached: boolean;
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
