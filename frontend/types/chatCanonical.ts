import type { BusinessCard } from "@/types/cards";
import type { ID } from "@/types/common";
import type { ReplyState } from "./inbox";
import type { BusinessTag, LostReason, PortraitStage } from "./portrait";

export type CustomerStage = "unknown" | "new" | "interested" | "negotiating" | "risk" | "done";
export type MessageRole = "unknown" | "buyer" | "seller" | "system" | "card";

export interface CustomerD90Behavior {
  productViews?: number;
  validInquiries?: number;
  repliedInquiries?: number;
  validRfqs?: number;
  loginDays?: number;
  spamInquiries?: number;
  blacklisted?: number;
}

export interface CustomerProfile {
  id: ID;
  aliId?: string;
  loginId?: string;
  encryptAccountId?: string;
  memberId?: string;
  name: string;
  firstName?: string;
  lastName?: string;
  company: string;
  country: string;
  registerDate?: string;
  email: string;
  mobile?: string;
  phone: string;
  stage: CustomerStage;
  tags: string[];
  // Portrait fields resolved by chatAdapter; older backends without portrait
  // support yield cid null, unknown stage, version 0 and portraitEditable false.
  cid: number | null;
  businessStage: PortraitStage;
  businessTags: BusinessTag[];
  businessVersion: number;
  lostReason: LostReason | null;
  lostNote: string | null;
  lostAt: number | null;
  portraitEditable: boolean;
  qualityTag?: string;
  growthLevel?: string;
  industries?: string[];
  availability: string;
  joiningYears?: number;
  potentialScore?: number;
  recentContact?: boolean;
  emailValidated?: boolean;
  behavior: string[];
  d90?: CustomerD90Behavior;
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
  replyState: ReplyState;
  pendingSince: number | null;
  dueAt: number | null;
  isOverdue: boolean;
  historyPending: boolean;
  uncertain: boolean;
  dialogueCount?: number;
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
  readSnapshot?: string;
  messages: ChatMessage[];
  analysis?: ConversationAnalysis;
}

export interface AssistantSuggestion {
  id: ID;
  title: string;
  content: string;
  tone: "formal" | "friendly" | "urgent" | string;
  zh?: string;
  /** The stored reply intent this suggestion came from. */
  intentId?: string | null;
  status?: "requested" | "accepted" | "discarded" | "simulated" | string;
}

export interface AssistantSuggestionResult {
  items: AssistantSuggestion[];
  batchId?: string;
  /** The Agent's own reasoning; replies live in ``items``, not in this text. */
  reasoning?: string;
  /** True when the run hit its tool budget and the result may be incomplete. */
  restricted?: boolean;
}
