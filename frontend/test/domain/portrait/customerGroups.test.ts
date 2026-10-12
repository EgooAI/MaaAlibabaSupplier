import { describe, expect, it } from "vitest";
import { customerLabelOf, groupCustomers } from "@/domain/portrait/customerGroups";
import type { Conversation } from "@/types/chatCanonical";
import type { ReplyState } from "@/types/inbox";

function conversation(id: string, cid: number | null, options: { unread?: number; reply?: ReplyState; name?: string } = {}): Conversation {
  return {
    id,
    customer: {
      id: cid === null ? `c-${id}` : String(cid),
      name: options.name ?? `buyer-${id}`,
      company: "Gulf Trade",
      country: "阿联酋",
      email: "buyer@example.com",
      phone: "+971 50 000 0000",
      stage: "unknown",
      tags: [],
      cid,
      businessStage: "unknown",
      businessTags: [],
      businessVersion: 0,
      lostReason: null,
      lostNote: null,
      lostAt: null,
      portraitEditable: cid !== null,
      availability: "",
      behavior: [],
    },
    latestMessage: `message-${id}`,
    updatedAt: "2026-09-17 10:00",
    unreadCount: options.unread ?? 0,
    replyState: options.reply ?? "none",
    pendingSince: null,
    dueAt: null,
    isOverdue: false,
    historyPending: false,
    uncertain: false,
  };
}

describe("customerGroups", () => {
  it("groups sessions by cid and aggregates unread and needs-reply", () => {
    const groups = groupCustomers([
      conversation("42", 1001, { unread: 2, reply: "needs_reply" }),
      conversation("43", 1001, { unread: 1 }),
      conversation("44", 1002),
    ]);
    expect(groups.map((group) => group.key)).toEqual(["c:1001", "c:1002"]);
    expect(groups[0].sessionIds).toEqual(["42", "43"]);
    expect(groups[0].unread).toBe(3);
    expect(groups[0].needsReply).toBe(1);
    expect(groups[0].representative.id).toBe("42");
    expect(groups[1].sessionIds).toEqual(["44"]);
    expect(customerLabelOf(groups[0])).toBe("buyer-42");
  });

  it("merges unresolved sessions into one trailing pseudo customer", () => {
    const groups = groupCustomers([
      conversation("n1", null, { unread: 2, reply: "needs_reply" }),
      conversation("42", 1001),
      conversation("n2", null),
    ]);
    expect(groups.map((group) => group.key)).toEqual(["c:1001", "na"]);
    const unresolved = groups[1];
    expect(unresolved.cid).toBeNull();
    expect(unresolved.sessionIds).toEqual(["n1", "n2"]);
    expect(unresolved.unread).toBe(2);
    expect(unresolved.needsReply).toBe(1);
    expect(customerLabelOf(unresolved)).toBe("未识别客户");
  });

  it("returns no groups for an empty page", () => {
    expect(groupCustomers([])).toEqual([]);
  });
});
