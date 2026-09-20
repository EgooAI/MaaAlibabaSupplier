import { describe, expect, it } from "vitest";
import { buildConversationExport, dateGroup, dialogueCountGroup, dialogueCountOf, groupConversations, isTranslatableMessage, mergeConversationDetail, mergeMessageTextTranslations, sortConversations } from "@/domain/chat/chatModel";
import type { ChatMessage, Conversation, ConversationDetail } from "@/types/chatCanonical";

const summary = (id: string, updatedAt: string, replyState: Conversation["replyState"] = "waiting_customer"): Conversation => ({
  id,
  customer: {
    id,
    aliId: `buyer-${id}`,
    name: `Buyer ${id}`,
    company: "Buyer Co.",
    country: "US",
    email: "buyer@example.com",
    phone: "+1 000",
    stage: "interested",
    tags: ["高质量买家"],
    availability: "当前可联系",
    behavior: [],
  },
  latestMessage: "最新消息",
  updatedAt,
  unreadCount: 0,
  replyState,
  pendingSince: null, dueAt: null, isOverdue: false, historyPending: false, uncertain: false,
});

const messages: ChatMessage[] = [
  { id: "message-1", role: "buyer", content: "需要报价", createdAt: "2026-09-08 10:10", read: false, sid: 42, externalMid: "message-1", senderAid: 101, type: "text", rawContent: { text: "需要报价" } },
  { id: "message-2", role: "seller", content: "已收到", createdAt: "2026-09-08 10:11", read: true, sid: 42, externalMid: "message-2", senderAid: 9001, type: "text", rawContent: "已收到" },
];

const detail: ConversationDetail = {
  ...summary("42", "2026-09-08 10:11", "needs_reply"),
  messages,
  analysis: {
    intent: "需要报价",
    stage: "interested",
    score: 85,
    risks: ["交期"],
    nextActions: ["发送报价"],
    summary: "客户处于高意向阶段。",
  },
};

describe("conversation domain model", () => {
  it("sorts and groups canonical conversations", () => {
    const conversations = [summary("old", "2026-09-06 10:00"), summary("new", "2026-09-08 10:00", "needs_reply")];
    expect(sortConversations(conversations).map((item) => item.id)).toEqual(["new", "old"]);
    expect(groupConversations(conversations, "status").map((group) => group.label)).toEqual(["等待客户", "待回复"]);
  });

  it("groups by dialogue count with fixed order", () => {
    const conversations = [
      { ...summary("short", "2026-09-08 10:00"), dialogueCount: 2 },
      { ...summary("long", "2026-09-08 10:00"), dialogueCount: 20 },
      { ...summary("mid", "2026-09-08 10:00"), dialogueCount: 5 },
      summary("unknown", "2026-09-08 10:00"),
    ];
    expect(dialogueCountOf(conversations[0])).toBe(2);
    expect(dialogueCountGroup(conversations[1])).toBe("长会话：16条及以上");
    expect(groupConversations(conversations, "count").map((group) => group.label)).toEqual([
      "长会话：16条及以上",
      "中等长度会话：4-15条",
      "短会话：1-3条",
    ]);
  });

  it("groups dates relative to the provided current date", () => {
    const now = new Date("2026-09-11T12:00:00+08:00");
    expect(dateGroup("2026-09-11 08:30", now)).toBe("今天");
    expect(dateGroup("2026-09-10 23:59", now)).toBe("昨天");
    expect(dateGroup("2026-09-07 10:00", now)).toBe("更早");
    expect(dateGroup("未知时间", now)).toBe("更早");
  });

  it("merges text-keyed translations for every party, keyed strictly by content", () => {
    const allParties: ChatMessage[] = [
      messages[0],
      messages[1],
      { ...messages[0], id: "message-3", role: "system", content: "auto reply", createdAt: "now" },
    ];
    const merged = mergeMessageTextTranslations(allParties, { "需要报价": "Quote needed", "已收到": "Received", "auto reply": "自动回复" });
    expect(merged[0]).toMatchObject({ id: "message-1", translatedContent: "Quote needed" });
    expect(merged[1]).toMatchObject({ id: "message-2", translatedContent: "Received" });
    expect(merged[2]).toMatchObject({ id: "message-3", translatedContent: "自动回复" });
    expect(mergeMessageTextTranslations(allParties, { "需要报价": null })[0].translatedContent).toBeUndefined();
    expect(mergeMessageTextTranslations(allParties, {})).toBe(allParties);
    const source = [{ ...messages[0], translatedContent: "Quote needed" }, messages[1]];
    expect(mergeMessageTextTranslations(source, { "需要报价": "Quote needed" })).toBe(source);
    const replaced = mergeMessageTextTranslations(source, { "需要报价": "Refreshed" });
    expect(replaced).not.toBe(source);
    expect(replaced[0].translatedContent).toBe("Refreshed");
    // 查询键来自 trim 后的文本：首尾带空白的消息也能命中。
    const padded = [{ ...messages[0], content: " 需要报价 " }];
    expect(mergeMessageTextTranslations(padded, { "需要报价": "Quote needed" })[0].translatedContent).toBe("Quote needed");
  });

  it("treats only textual, card-free messages as translatable", () => {
    expect(isTranslatableMessage(messages[0])).toBe(true);
    expect(isTranslatableMessage({ ...messages[0], role: "card" })).toBe(false);
    // 携带卡片载荷的消息即使角色不是卡片也不可翻译（UI 不渲染其译文）。
    expect(isTranslatableMessage({ ...messages[0], card: { id: "card-1" } as ChatMessage["card"] })).toBe(false);
    expect(isTranslatableMessage({ ...messages[0], content: "   " })).toBe(false);
  });

  it("merges incoming conversation details without dropping local translations or analysis", () => {
    const incoming: ConversationDetail = { ...detail, messages: messages.map((item) => ({ ...item })), analysis: undefined };
    const current: ConversationDetail = { ...detail, messages: [{ ...messages[0], translatedContent: "Quote needed" }, messages[1]] };

    const merged = mergeConversationDetail(current, incoming);

    expect(merged.analysis).toBe(detail.analysis);
    expect(merged.messages[0]).toMatchObject({ id: "message-1", translatedContent: "Quote needed" });
  });

  it("keeps local analysis and drops translations for edited messages on refresh", () => {
    const incoming: ConversationDetail = {
      ...detail,
      messages: [{ ...messages[0], content: "需要报价（已编辑）" }, messages[1]],
      analysis: { ...detail.analysis!, summary: "服务端新分析" },
    };
    const current: ConversationDetail = { ...detail, messages: [{ ...messages[0], translatedContent: "Quote needed" }, messages[1]] };

    const merged = mergeConversationDetail(current, incoming);

    // 本地已有人工/异步结果时优先保留，同时防止旧译文套用到已编辑内容。
    expect(merged.analysis).toBe(detail.analysis);
    expect(merged.messages[0].translatedContent).toBeUndefined();
    expect(merged.messages[0].content).toBe("需要报价（已编辑）");
  });

  it("exports canonical details", () => {
    expect(buildConversationExport([detail]).content).toContain("客户：Buyer 42");
    expect(buildConversationExport([detail]).content).toContain("[2026-09-08 10:10] buyer: 需要报价");
    expect(buildConversationExport([detail])).not.toHaveProperty("archiveName");
    const translated: ConversationDetail = {
      ...detail,
      messages: [{ ...messages[0], translatedContent: "Quote needed" }, messages[1]],
    };
    expect(buildConversationExport([translated]).content).toContain("译文：Quote needed");
  });
});
