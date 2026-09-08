import { describe, expect, it } from "vitest";
import { buildConversationExport, conversationTimeLabel, dateGroup, groupConversations, mergeConversationDetail, mergeMessageTranslations, messageExecutionState, sortConversations, stageLabel, statusLabel } from "@/domain/chat/chatModel";
import type { ChatMessage, Conversation, ConversationDetail } from "@/types/chatCanonical";

const summary = (id: string, updatedAt: string, status: Conversation["status"] = "following"): Conversation => ({
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
  unreadCount: status === "unread" ? 1 : 0,
  status,
  priority: "high",
});

const messages: ChatMessage[] = [
  { id: "message-1", role: "buyer", content: "需要报价", createdAt: "2026-09-08 10:10", read: false, sid: 42, externalMid: "message-1", senderAid: 101, type: "text", rawContent: { text: "需要报价" } },
  { id: "message-2", role: "seller", content: "已收到", createdAt: "2026-09-08 10:11", read: true, sid: 42, externalMid: "message-2", senderAid: 9001, type: "text", rawContent: "已收到" },
];

const detail: ConversationDetail = {
  ...summary("42", "2026-09-08 10:11", "unread"),
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
    const conversations = [summary("old", "2026-09-06 10:00"), summary("new", "2026-09-08 10:00", "unread")];
    expect(sortConversations(conversations).map((item) => item.id)).toEqual(["new", "old"]);
    expect(groupConversations(conversations, "status").map((group) => group.label)).toEqual(["跟进中", "未读待回"]);
    expect(statusLabel("closed")).toBe("已关闭");
  });

  it("groups dates relative to the provided current date", () => {
    const now = new Date("2026-09-11T12:00:00+08:00");
    expect(dateGroup("2026-09-11 08:30", now)).toBe("今天");
    expect(dateGroup("2026-09-10 23:59", now)).toBe("昨天");
    expect(dateGroup("2026-09-07 10:00", now)).toBe("更早");
    expect(dateGroup("未知时间", now)).toBe("更早");
  });

  it("formats conversation time without slicing invalid text", () => {
    expect(conversationTimeLabel("2026-09-11 08:30")).toBe("09-11 08:30");
    expect(conversationTimeLabel("未知时间")).toBe("未知时间");
  });

  it("merges message translations by message id", () => {
    const merged = mergeMessageTranslations(messages, [{ messageId: "message-1", translatedContent: "Quote needed" }]);
    expect(merged[0]).toMatchObject({ id: "message-1", translatedContent: "Quote needed" });
    expect(merged[1]).toBe(messages[1]);
  });

  it("merges incoming conversation details without dropping local translations or analysis", () => {
    const incoming: ConversationDetail = { ...detail, messages: messages.map((item) => ({ ...item })), analysis: undefined };
    const current: ConversationDetail = { ...detail, messages: [{ ...messages[0], translatedContent: "Quote needed" }, messages[1]] };

    const merged = mergeConversationDetail(current, incoming);

    expect(merged.analysis).toBe(detail.analysis);
    expect(merged.messages[0]).toMatchObject({ id: "message-1", translatedContent: "Quote needed" });
  });

  it("classifies message execution terminal states", () => {
    expect(messageExecutionState({ success: true, message: "queued", task_snapshot: { task_id: "1", description: "", status: "pending", message: "queued", result: null, created_at: 0, started_at: null, completed_at: null } })).toBe("pending");
    expect(messageExecutionState({ success: true, message: "running", task_snapshot: { task_id: "2", description: "", status: "running", message: "running", result: null, created_at: 0, started_at: 0, completed_at: null } })).toBe("pending");
    expect(messageExecutionState({ success: true, message: "ok", task_snapshot: { task_id: "3", description: "", status: "succeeded", message: "ok", result: null, created_at: 0, started_at: 0, completed_at: 1 } })).toBe("succeeded");
    expect(messageExecutionState({ success: false, message: "failed", task_snapshot: null })).toBe("failed");
  });

  it("labels unknown customer stages explicitly", () => {
    expect(stageLabel("unknown")).toBe("未知阶段");
  });

  it("exports canonical details", () => {
    expect(buildConversationExport([detail]).content).toContain("客户：Buyer 42");
    expect(buildConversationExport([detail]).content).toContain("[2026-09-08 10:10] buyer: 需要报价");
    expect(buildConversationExport([detail])).not.toHaveProperty("archiveName");
    expect(stageLabel("interested")).toBe("高意向");
  });
});
