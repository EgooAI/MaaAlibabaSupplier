import { describe, expect, it } from "vitest";
import { inboxQueryFromUrl, inboxQueryToSearchParams, inboxQueryToUrl, replyStates } from "@/domain/chat/inboxModel";
import type { ConversationQuery } from "@/types/inbox";

describe("inbox query url", () => {
  it("parses applied filters from the url and drops unknown values", () => {
    const params = new URLSearchParams();
    params.set("q", "lamp");
    params.set("search_scope", "messages");
    params.set("country", "Spain");
    params.set("tag", "金牌");
    params.set("reply_state", "needs_reply");
    params.set("unread", "true");
    params.set("overdue", "false");
    params.append("portrait_stage", "won");
    params.append("portrait_stage", "following_up");
    params.append("portrait_stage", "bogus");
    params.append("business_tag", "t1");
    params.append("business_tag", "t1");
    params.set("lost_reason", "price");
    params.set("portrait_completeness", "maintained");
    const query = inboxQueryFromUrl(params);
    expect(query).toEqual({
      q: "lamp",
      search_scope: "messages",
      country: "Spain",
      tag: "金牌",
      reply_state: "needs_reply",
      unread: true,
      overdue: false,
      portrait_stage: ["won", "following_up"],
      business_tag: ["t1"],
      lost_reason: "price",
      portrait_completeness: "maintained",
    });
  });

  it("treats unknown stages, reasons and completeness as no filter", () => {
    const params = new URLSearchParams();
    params.append("portrait_stage", "bogus");
    params.set("lost_reason", "bogus");
    params.set("portrait_completeness", "bogus");
    expect(inboxQueryFromUrl(params)).toEqual({
      search_scope: "all",
      portrait_stage: undefined,
      business_tag: undefined,
      lost_reason: undefined,
      portrait_completeness: undefined,
    });
  });

  it("round-trips applied filters without pagination tokens", () => {
    const query: ConversationQuery = {
      q: "lamp",
      search_scope: "all",
      country: "Spain",
      tag: "金牌",
      reply_state: "waiting_customer",
      unread: false,
      overdue: true,
      portrait_stage: ["won", "lost"],
      business_tag: ["t1", "t2"],
      lost_reason: "price",
      portrait_completeness: "unmaintained",
      offset: 50,
      limit: 50,
      pagination_revision: "page-token",
    };
    const restored = inboxQueryFromUrl(inboxQueryToSearchParams(query));
    expect(restored).toEqual({ ...query, offset: undefined, limit: undefined, pagination_revision: undefined });
    // Serializing the restored query again is idempotent: refresh keeps the view.
    expect(inboxQueryToSearchParams(restored).toString()).toEqual(inboxQueryToSearchParams(query).toString());
  });

  it("adds the conversation deep link only for the page path", () => {
    const query: ConversationQuery = { portrait_stage: ["won"] };
    expect(inboxQueryToUrl(query, "42", "/chat")).toBe("/chat?portrait_stage=won&conversation=42");
    expect(inboxQueryToUrl({}, "42", "/batch")).toBe("/batch?conversation=42");
    expect(inboxQueryToUrl({})).toBe("?");
  });

  it("keeps the documented reply state vocabulary", () => {
    expect(replyStates).toEqual(["needs_reply", "waiting_customer", "history_pending", "none", "unknown"]);
  });
});
