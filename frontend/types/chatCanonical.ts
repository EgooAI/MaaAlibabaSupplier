import type { BusinessCard } from "@/types/cards";
import type { ID } from "@/types/common";

export type ConversationStatus = "unread" | "following" | "waiting" | "closed";
export type CustomerStage = "unknown" | "new" | "interested" | "negotiating" | "risk" | "done";
export type MessageRole = "unknown" | "buyer" | "seller" | "system" | "card";

export interface CustomerProfile {
  id: ID;
  aliId?: string;
  name: string;
  company: string;
  country: string;
  email: string;
  phone: string;
  stage: CustomerStage;
  tags: string[];
  availability: string;
  behavior: string[];
}

export interface ChatMessage {
  id: ID;
  role: MessageRole;
  content: string;
  createdAt: string;
  sid?: number;
  externalMid?: string;
  senderAid?: number;
  read?: boolean | null;
  type?: string;
  rawContent?: unknown;
  translatedContent?: string;
  card?: BusinessCard;
}

export interface Conversation {
  id: ID;
  customer: CustomerProfile;
  latestMessage: string;
  updatedAt: string;
  unreadCount: number;
  status: ConversationStatus;
  priority: "high" | "medium" | "low";
}

export interface ConversationAnalysis {
  intent: string;
  stage: CustomerStage;
  score: number;
  risks: string[];
  nextActions: string[];
  summary: string;
  rawText?: string;
  jsonPayload?: Record<string, unknown> | null;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
  analysis?: ConversationAnalysis;
}

export interface AssistantSuggestion {
  id: ID;
  title: string;
  content: string;
  tone: "formal" | "friendly" | "urgent";
  zh?: string;
}
