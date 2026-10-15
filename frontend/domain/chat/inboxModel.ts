import type { ConversationQuery, ReplyState } from "@/types/inbox";
import { LOST_REASON_OPTIONS, PORTRAIT_STAGE_OPTIONS } from "@/domain/portrait/portraitModel";
import type { LostReason, PortraitStage } from "@/types/portrait";

export const replyStates: ReplyState[] = ["needs_reply", "waiting_customer", "history_pending", "none", "unknown"];

const portraitStageValues = new Set(PORTRAIT_STAGE_OPTIONS.map((option) => option.value));
const lostReasonValues = new Set(LOST_REASON_OPTIONS.map((option) => option.value));

export function inboxQueryFromUrl(params: URLSearchParams): ConversationQuery {
  const state = params.get("reply_state");
  const scope = params.get("search_scope");
  const completeness = params.get("portrait_completeness");
  const portraitStage = params.getAll("portrait_stage")
    .filter((value): value is PortraitStage => portraitStageValues.has(value as PortraitStage));
  const businessTag = [...new Set(params.getAll("business_tag").filter(Boolean))];
  const lostReason = params.get("lost_reason");
  return {
    q: params.get("q") || undefined,
    search_scope: scope === "customer" || scope === "messages" ? scope : "all",
    country: params.get("country") || undefined,
    tag: params.get("tag") || undefined,
    reply_state: replyStates.includes(state as ReplyState) ? state as ReplyState : undefined,
    unread: params.has("unread") ? params.get("unread") === "true" : undefined,
    overdue: params.has("overdue") ? params.get("overdue") === "true" : undefined,
    portrait_stage: portraitStage.length ? portraitStage : undefined,
    business_tag: businessTag.length ? businessTag : undefined,
    lost_reason: lostReason && lostReasonValues.has(lostReason as LostReason) ? lostReason as LostReason : undefined,
    portrait_completeness: completeness === "maintained" || completeness === "unmaintained" ? completeness : undefined,
  };
}

/** Conversational filters serialize into the same URL shape as downloads
 * and page links, so refreshing or sharing the list keeps the applied view.
 * Pagination tokens stay out on purpose: they always refer to one data set.
 */
export function inboxQueryToSearchParams(query: ConversationQuery): URLSearchParams {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.search_scope && query.search_scope !== "all") params.set("search_scope", query.search_scope);
  if (query.country) params.set("country", query.country);
  if (query.tag) params.set("tag", query.tag);
  if (query.reply_state) params.set("reply_state", query.reply_state);
  if (query.unread !== undefined) params.set("unread", String(query.unread));
  if (query.overdue !== undefined) params.set("overdue", String(query.overdue));
  for (const stage of query.portrait_stage ?? []) params.append("portrait_stage", stage);
  if (query.lost_reason) params.set("lost_reason", query.lost_reason);
  for (const tag of query.business_tag ?? []) params.append("business_tag", tag);
  if (query.portrait_completeness && query.portrait_completeness !== "all") {
    params.set("portrait_completeness", query.portrait_completeness);
  }
  return params;
}

/** 已应用筛选 + 可选会话深链组合成一个相对 URL（无 pathname 时为 "?..."）。
 * 分页令牌不写入 URL：它只属于一次数据集快照。 */
export function inboxQueryToUrl(query: ConversationQuery, conversation?: string | null, pathname = ""): string {
  const params = inboxQueryToSearchParams(query);
  if (conversation) params.set("conversation", conversation);
  const search = params.toString();
  if (pathname) return search ? `${pathname}?${search}` : pathname;
  return search ? `?${search}` : "?";
}

export const inboxCards = [
  { key: "needs_reply", label: "待回复", query: "reply_state=needs_reply" },
  { key: "overdue", label: "超时", query: "reply_state=needs_reply&overdue=true" },
  { key: "unread", label: "本工作台未读", query: "unread=true" },
  { key: "history_pending", label: "历史待确认", query: "reply_state=history_pending" },
  { key: "waiting_customer", label: "等待客户", query: "reply_state=waiting_customer" },
] as const;
