import type { BusinessCard } from "@/types/cards";
import type { ConversationStatus, CustomerStage } from "@/types/chatCanonical";
import type { ID } from "@/types/common";
import type { TaskSnapshot } from "@/types/status";

export type TransportMessageRole = "buyer" | "seller" | "system" | "card";

/** MaaAlibabaSupplier crm_sdk.models.session_meta.SessionMeta 的序列化形状。 */
export interface SessionMeta {
  sid: number;
  name: string | null;
  participants: number[];
}

/** MaaAlibabaSupplier crm_sdk.models.message.Message 的序列化形状。 */
export interface Message {
  external_mid: string;
  sid: number;
  sender: number;
  read: boolean | null;
  content: unknown;
  type: string;
}

/** MaaAlibabaSupplier crm_sdk.models.account.Account 的序列化形状。 */
export interface Account {
  aid: number;
  cid: number;
  pid: string | null;
  account: string | null;
  nickname: string | null;
  avatar: string | null;
  sids: number[] | null;
  extra?: Record<string, unknown> | null;
  created_time?: string;
  updated_time?: string;
}

/** MaaAlibabaSupplier crm_sdk.models.customer.Customer 的序列化形状。 */
export interface Customer {
  cid: number;
  name: string | null;
  sex?: string | null;
  birthdate?: string | null;
  region?: string | null;
  extra?: Record<string, unknown> | null;
  image?: Record<string, unknown> | null;
  created_time?: string;
  updated_time?: string;
}

/** MaaAlibabaSupplier crm_sdk.models.platform.Platform 的序列化形状。 */
export interface Platform {
  pid: string;
  name: string;
  extra?: Record<string, unknown> | null;
  created_time?: string;
  updated_time?: string;
}

/** MaaAlibabaSupplier crm_sdk.models.account_mapping.AccountMapping 的序列化形状。 */
export interface AccountMapping {
  amid: number;
  aid: number;
  type: string | null;
  key: string | null;
}

export interface ConversationMessageDto {
  message: Message;
  created_at: string | null;
  role?: TransportMessageRole;
}

export interface CustomerViewDto {
  id: ID;
  ali_id: string | null;
  login_id?: string | null;
  encrypt_account_id?: string | null;
  member_id?: string | null;
  name: string;
  first_name?: string | null;
  last_name?: string | null;
  company: string;
  country: string;
  register_date?: string | null;
  email: string;
  mobile?: string | null;
  phone: string;
  stage: CustomerStage;
  tags: string[];
  quality_tag?: string | null;
  growth_level?: string | null;
  industries?: string[];
  availability: string;
  joining_years?: number | null;
  potential_score?: number | null;
  recent_contact?: boolean | null;
  email_validated?: boolean | null;
  behavior: string[];
  d90?: {
    product_views?: number;
    valid_inquiries?: number;
    replied_inquiries?: number;
    valid_rfqs?: number;
    login_days?: number;
    spam_inquiries?: number;
    blacklisted?: number;
  } | null;
}

export interface ConversationLatestDto {
  content: string | null;
  updated_at: string | null;
}

export interface ConversationAnalysisDto {
  intent: string;
  stage: CustomerStage;
  score: number;
  risks: string[];
  next_actions: string[];
  summary: string;
  raw_text?: string;
  json_payload?: Record<string, unknown> | null;
}

/** 后端基于 SDK 实体生成的会话聚合响应。 */
export interface ConversationAggregateDto extends SessionMeta {
  messages: ConversationMessageDto[];
  accounts?: Account[];
  customers?: Customer[];
  platforms?: Platform[];
  account_mappings?: AccountMapping[];
  customer_view?: CustomerViewDto;
  latest: ConversationLatestDto;
  unread_count: number;
  status: ConversationStatus;
  priority: "high" | "medium" | "low";
  dialogue_count?: number;
  analysis?: ConversationAnalysisDto;
  business_cards?: BusinessCard[];
}

export interface ConversationSendResultDto {
  message?: ConversationMessageDto;
  conversation: ConversationAggregateDto;
  execution: {
    success: boolean;
    message: string;
    task_snapshot: TaskSnapshot | null;
  };
}
