import { describe, expect, it } from "vitest";
import { adaptConversationDetail, adaptConversationSummary } from "@/services/chatAdapter";
import type { ConversationAggregateDto } from "@/types/chatTransport";

// Backend-shaped aggregate: normalized text/card/system types, explicit roles,
// snake_case customer_view with d90, business_cards attached.
const aggregate: ConversationAggregateDto = {
  sid: 7,
  name: "alibaba_icbu:10001:20002",
  participants: [11, 22],
  accounts: [
    { aid: 22, cid: 202, pid: "alibaba_icbu", account: "buyer", nickname: "Buy Er", avatar: null, sids: [7], extra: { ali_id: "20002" } },
    { aid: 11, cid: 101, pid: "alibaba_icbu", account: "seller", nickname: "Seller", avatar: null, sids: [7], extra: { is_self: true } },
  ],
  customers: [{ cid: 202, name: "Buy Er", region: "ES" }],
  customer_view: {
    id: "202",
    ali_id: "20002",
    login_id: "buyer",
    encrypt_account_id: null,
    member_id: "m-1",
    name: "Buy Er",
    first_name: "Buy",
    last_name: "Er",
    company: "Luma",
    country: "ES",
    register_date: "2020-01-01 00:00:00",
    email: "buy@luma.example",
    mobile: "+34 1",
    phone: "+34 2",
    stage: "unknown",
    tags: ["A"],
    quality_tag: "A",
    growth_level: "G1",
    industries: ["lights"],
    availability: "可用",
    joining_years: 3,
    potential_score: 80,
    recent_contact: true,
    email_validated: true,
    behavior: [],
    d90: { product_views: 12, valid_inquiries: 4, replied_inquiries: 3, valid_rfqs: 1, login_days: 28, spam_inquiries: 0, blacklisted: 0 },
  },
  latest: { content: "hello", updated_at: "2026-09-01 10:00:00" },
  unread_count: 0,
  status: "following",
  priority: "medium",
  business_cards: [
    { id: "P123", title: "Lamp", type: "product", summary: "$1 · MOQ 10pcs", tags: ["产品卡"], coverTone: "#e6f4ff", details: [{ label: "价格", value: "$1" }] },
  ],
  messages: [
    {
      message: { external_mid: "msg_table:m1", sid: 7, sender: 22, read: null, content: "hello, quote please", type: "text" },
      created_at: "2026-09-01 10:00:00",
      role: "buyer",
    },
    {
      message: { external_mid: "msg_table:m2", sid: 7, sender: 11, read: null, content: "sure", type: "text" },
      created_at: "2026-09-01 10:01:00",
      role: "seller",
    },
    {
      message: { external_mid: "msg_table:m3", sid: 7, sender: 11, read: null, content: "sys", type: "system" },
      created_at: "2026-09-01 10:02:00",
      role: "system",
    },
    {
      message: { external_mid: "msg_table:m4", sid: 7, sender: 22, read: null, content: { card_id: "P123", label: "产品卡" }, type: "card" },
      created_at: "2026-09-01 10:03:00",
      role: "card",
    },
  ],
};

describe("backend conversation DTO", () => {
  it("maps summary and keeps d90 customer fields", () => {
    const summary = adaptConversationSummary(aggregate);
    expect(summary.id).toBe("7");
    expect(summary.customer.aliId).toBe("20002");
    expect(summary.customer.loginId).toBe("buyer");
    expect(summary.customer.d90?.productViews).toBe(12);
    expect(summary.customer.joiningYears).toBe(3);
  });

  it("maps message roles and links business cards", () => {
    const detail = adaptConversationDetail(aggregate);
    expect(detail.messages.map((item) => item.role)).toEqual(["buyer", "seller", "system", "card"]);
    expect(detail.messages[3].card?.id).toBe("P123");
    expect(detail.messages[0].content).toBe("hello, quote please");
  });

  it("falls back when customer_view is missing", () => {
    const { customer_view: _dropped, ...rest } = aggregate;
    const detail = adaptConversationDetail({ ...rest, accounts: [], customers: [], participants: [] });
    expect(detail.customer.name).toBe("未知客户");
  });
});
