import type { BusinessCard } from "@/types/cards";
import { formatDateTime } from "@/domain/time";
import type { ConversationAnalysis, ConversationDetail, Conversation, ChatMessage, CustomerProfile } from "@/types/chatCanonical";
import type { ConversationAggregateDto, ConversationAnalysisDto, ConversationMessageDto, ConversationSendResultDto, CustomerViewDto } from "@/types/chatTransport";

interface ChatAdapterOptions {
  cards?: BusinessCard[];
}

export function adaptConversationSummary(aggregate: ConversationAggregateDto): Conversation {
  const customer = adaptCustomer(aggregate);
  return {
    id: String(aggregate.sid),
    customer,
    latestMessage: aggregate.latest?.content ?? "",
    updatedAt: formatDateTime(aggregate.latest?.updated_at ?? null),
    unreadCount: aggregate.unread_count ?? 0,
    status: aggregate.status ?? "following",
    priority: aggregate.priority ?? "medium",
    dialogueCount: aggregate.dialogue_count ?? undefined,
  };
}

export function adaptConversationDetail(aggregate: ConversationAggregateDto | undefined, options: ChatAdapterOptions = {}): ConversationDetail {
  if (!aggregate) throw new Error("会话聚合数据为空");
  const summary = adaptConversationSummary(aggregate);
  const cards = aggregate.business_cards ?? options.cards ?? [];
  const messages = (aggregate.messages ?? []).map((item) => adaptMessage(item, cards));
  return {
    ...summary,
    messages,
    analysis: adaptAnalysis(aggregate.analysis),
  };
}

export function adaptSendMessageResult(input: ConversationSendResultDto, options: ChatAdapterOptions = {}) {
  const conversation = adaptConversationDetail(input.conversation, options);
  const cards = input.conversation.business_cards ?? options.cards ?? [];
  const message = input.message ? adaptMessage(input.message, cards) : undefined;
  return { message, conversation, execution: input.execution };
}

function adaptCustomer(aggregate: ConversationAggregateDto): CustomerProfile {
  const view = aggregate.customer_view ?? deriveCustomerView(aggregate);
  return {
    id: view.id,
    aliId: view.ali_id ?? undefined,
    loginId: view.login_id ?? undefined,
    encryptAccountId: view.encrypt_account_id ?? undefined,
    memberId: view.member_id ?? undefined,
    name: view.name,
    firstName: view.first_name ?? undefined,
    lastName: view.last_name ?? undefined,
    company: view.company,
    country: view.country,
    registerDate: view.register_date ?? undefined,
    email: view.email,
    mobile: view.mobile ?? undefined,
    phone: view.phone,
    stage: view.stage,
    tags: view.tags,
    qualityTag: view.quality_tag ?? undefined,
    growthLevel: view.growth_level ?? undefined,
    industries: view.industries,
    availability: view.availability,
    joiningYears: view.joining_years ?? undefined,
    potentialScore: view.potential_score ?? undefined,
    recentContact: view.recent_contact ?? undefined,
    emailValidated: view.email_validated ?? undefined,
    behavior: view.behavior,
    d90: view.d90 ? {
      productViews: view.d90.product_views,
      validInquiries: view.d90.valid_inquiries,
      repliedInquiries: view.d90.replied_inquiries,
      validRfqs: view.d90.valid_rfqs,
      loginDays: view.d90.login_days,
      spamInquiries: view.d90.spam_inquiries,
      blacklisted: view.d90.blacklisted,
    } : undefined,
  };
}

function deriveCustomerView(aggregate: ConversationAggregateDto): CustomerViewDto {
  const accounts = aggregate.accounts ?? [];
  const customers = aggregate.customers ?? [];
  const participantIds = new Set(aggregate.participants ?? []);
  const buyerAid = (aggregate.messages ?? []).find((item) => item.role === "buyer")?.message.sender;
  const account = buyerAid === undefined ? undefined : accounts.find((item) => item.aid === buyerAid && participantIds.has(item.aid));
  const customer = account ? customers.find((item) => item.cid === account.cid) : undefined;
  const extra = customer?.extra ?? {};
  const accountExtra = account?.extra ?? {};
  return {
    id: String(customer?.cid ?? account?.cid ?? aggregate.sid),
    ali_id: account?.account ?? null,
    name: customer?.name ?? account?.nickname ?? account?.account ?? "未知客户",
    company: stringValue(extra.company) ?? "",
    country: customer?.region ?? "",
    email: stringValue(accountExtra.email) ?? "",
    phone: stringValue(accountExtra.phone) ?? "",
    stage: "unknown" as const,
    tags: [],
    availability: "",
    behavior: [],
  };
}

function adaptMessage(item: ConversationMessageDto, cards: BusinessCard[]): ChatMessage {
  const message = item.message;
  const cardId = contentCardId(message.content);
  const card = cardId ? cards.find((candidate) => candidate.id === cardId) : undefined;
  return {
    id: message.external_mid,
    role: item.role ?? "unknown",
    content: displayContent(message.content, card),
    createdAt: formatDateTime(item.created_at),
    sid: message.sid,
    externalMid: message.external_mid,
    senderAid: message.sender,
    read: message.read,
    type: message.type,
    rawContent: message.content,
    card,
  };
}

function adaptAnalysis(input: ConversationAnalysisDto | undefined): ConversationAnalysis | undefined {
  if (!input) return undefined;
  return {
    intent: input.intent,
    stage: input.stage,
    score: input.score,
    risks: input.risks,
    nextActions: input.next_actions,
    summary: input.summary,
    rawText: input.raw_text,
    jsonPayload: input.json_payload,
  };
}

function contentCardId(content: unknown) {
  if (!content || typeof content !== "object" || !("card_id" in content)) return undefined;
  return typeof content.card_id === "string" ? content.card_id : undefined;
}

function displayContent(content: unknown, card?: BusinessCard) {
  if (typeof content === "string") return content;
  if (content && typeof content === "object") {
    if ("label" in content && typeof content.label === "string") return content.label;
    if ("text" in content && typeof content.text === "string") return content.text;
  }
  if (content === null || content === undefined) return card ? "系统推荐卡片" : "";
  try {
    return JSON.stringify(content);
  } catch {
    return card ? "系统推荐卡片" : "";
  }
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.trim() ? value : undefined;
}
