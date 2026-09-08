import type { ChatMessage, ConversationDetail } from "./chatCanonical";
import type { TaskSnapshot } from "@/types/status";

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
  action?: "test" | "send";
}

export interface MessageExecution {
  success: boolean;
  message: string;
  task_snapshot: TaskSnapshot | null;
}

export interface SendMessageResult {
  message?: ChatMessage;
  conversation: ConversationDetail;
  execution: MessageExecution;
}

export interface ExportConversationsInput {
  conversationIds: string[];
}

export interface ExportConversationsResult {
  fileName: string;
  content: string;
  archiveName?: string;
}
