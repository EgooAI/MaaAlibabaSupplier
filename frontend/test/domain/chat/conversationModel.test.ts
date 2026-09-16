import { describe, expect, it } from "vitest";
import { buildConversationExport, dateGroup, dialogueCountGroup, dialogueCountOf, groupConversations, mergeConversationDetail, mergeMessageTranslations, messageExecutionState, sortConversations } from "@/domain/chat/chatModel";
import type { ChatMessage, Conversation, ConversationDetail } from "@/types/chatCanonical";
import type { MessageExecution } from "@/types/chatOperations";

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

  it.each([false, null, true])("uses task status before execution.success=%s", (success) => {
    for (const status of ["pending", "running", "failed", "succeeded"] as const) {
      const terminal = status === "failed" || status === "succeeded";
      expect(messageExecutionState({
        success,
        message: status,
        task_snapshot: {
          task_id: "task-1", description: "", status, message: status,
          result: terminal ? [status === "succeeded", status] : null,
          created_at: 0, started_at: status === "pending" ? null : 1, completed_at: terminal ? 2 : null,
        },
      })).toBe(terminal ? status : "pending");
    }
  });

  it.each([null, undefined])("does not infer success from a missing result with snapshot=%s", (task_snapshot) => {
    // Older or incomplete responses may omit the now-required snapshot.
    for (const success of [null, false, true]) {
      const execution = { success, message: "", task_snapshot } as unknown as MessageExecution;
      expect(messageExecutionState(execution)).toBe(success === null ? "pending" : success ? "succeeded" : "failed");
    }
  });

  it("exports canonical details", () => {
    expect(buildConversationExport([detail]).content).toContain("客户：Buyer 42");
    expect(buildConversationExport([detail]).content).toContain("[2026-09-08 10:10] buyer: 需要报价");
    expect(buildConversationExport([detail])).not.toHaveProperty("archiveName");
  });
});
