import { mockSelfInfo } from "@/mock/selfData";
import type { Account, AccountMapping, ConversationAggregateDto, Customer, Platform } from "@/types/chatTransport";
import type { AssistantSuggestion } from "@/types/chatCanonical";

const customers: Customer[] = [
  { cid: 1001, name: "Sofia Martinez", region: "Spain", extra: { company: "Luma Retail Group" } },
  { cid: 1002, name: "Ahmed Khan", region: "UAE", extra: { company: "Gulf Fresh Logistics" } },
  { cid: 1003, name: "Maya Brown", region: "Canada", extra: { company: "North Harbor Supplies" } },
  { cid: 9001, name: "Demo Seller", region: "China", extra: { company: "Hangzhou Smart Export Co., Ltd." } },
];

const platforms: Platform[] = [
  { pid: "alibaba", name: "Alibaba.com", extra: null },
];

const accounts: Account[] = [
  { aid: 101, cid: 1001, pid: "alibaba", account: "buyer-ali-001", nickname: "Sofia Martinez", avatar: null, sids: [42], extra: { email: "sofia@luma.example", phone: "+34 600 123 456" } },
  { aid: 102, cid: 1002, pid: "alibaba", account: "buyer-ali-002", nickname: "Ahmed Khan", avatar: null, sids: [43], extra: { email: "ahmed@gfresh.example", phone: "+971 55 000 1188" } },
  { aid: 103, cid: 1003, pid: "alibaba", account: "buyer-ali-003", nickname: "Maya Brown", avatar: null, sids: [44], extra: { email: "maya@northharbor.example", phone: "+1 604 000 9281" } },
  { aid: 9001, cid: 9001, pid: "alibaba", account: mockSelfInfo.ali_id, nickname: "Demo Seller", avatar: mockSelfInfo.avatar_url, sids: [42, 43, 44], extra: null },
];

const accountMappings: AccountMapping[] = [
  { amid: 1, aid: 101, type: "account", key: "buyer-ali-001" },
  { amid: 2, aid: 102, type: "account", key: "buyer-ali-002" },
  { amid: 3, aid: 103, type: "account", key: "buyer-ali-003" },
  { amid: 4, aid: 9001, type: "account", key: mockSelfInfo.ali_id },
];

function aggregateEntities(aid: number) {
  const participantIds = new Set([aid, mockSelfInfo.aid]);
  const participantAccounts = accounts.filter((account) => participantIds.has(account.aid));
  const participantCids = new Set(participantAccounts.map((account) => account.cid));
  return {
    accounts: participantAccounts,
    customers: customers.filter((customer) => participantCids.has(customer.cid)),
    platforms,
    account_mappings: accountMappings.filter((mapping) => participantIds.has(mapping.aid)),
  };
}

export const conversationAggregates: ConversationAggregateDto[] = [
  {
    sid: 42,
    name: "Luma Retail Group",
    participants: [101, 9001],
    ...aggregateEntities(101),
    customer_view: {
      id: "1001",
      ali_id: "buyer-ali-001",
      name: "Sofia Martinez",
      company: "Luma Retail Group",
      country: "Spain",
      email: "sofia@luma.example",
      phone: "+34 600 123 456",
      stage: "interested",
      tags: ["高质量买家", "新能源", "户外照明"],
      availability: "当前可联系",
      behavior: ["商品浏览 12 次", "有效询盘 4 条", "活跃 28 天", "近期已联系"],
    },
    latest: { updated_at: "2026-09-07 10:21", content: "客户浏览了 3 个太阳能灯 SKU，并下载认证附件。" },
    unread_count: 1,
    status: "unread",
    priority: "high",
    analysis: {
      intent: "客户浏览了 3 个太阳能灯 SKU，并下载认证附件。",
      stage: "interested",
      score: 88,
      risks: ["需持续跟进响应时效"],
      next_actions: ["确认采购数量", "发送报价与认证资料"],
      summary: "Sofia Martinez 当前处于高意向阶段，建议优先处理最近消息。",
    },
    messages: [
      { message: { external_mid: "msg-001", sid: 42, sender: 101, read: false, content: "Hi, could you confirm the MOQ and lead time for the solar lights?", type: "text" }, created_at: "2026-09-07 10:10", role: "buyer" },
      { message: { external_mid: "msg-002", sid: 42, sender: 9001, read: true, content: { card_id: "inq-20260907-001", label: "系统推荐卡片" }, type: "card" }, created_at: "2026-09-07 10:15", role: "card" },
      { message: { external_mid: "msg-003", sid: 42, sender: 9001, read: true, content: "客户浏览了 3 个太阳能灯 SKU，并下载认证附件。", type: "system" }, created_at: "2026-09-07 10:21", role: "system" },
    ],
  },
  {
    sid: 43,
    name: "Gulf Fresh Logistics",
    participants: [102, 9001],
    ...aggregateEntities(102),
    customer_view: {
      id: "1002",
      ali_id: "buyer-ali-002",
      name: "Ahmed Khan",
      company: "Gulf Fresh Logistics",
      country: "UAE",
      email: "ahmed@gfresh.example",
      phone: "+971 55 000 1188",
      stage: "negotiating",
      tags: ["高潜买家", "冷链", "IoT"],
      availability: "当前可联系",
      behavior: ["商品浏览 9 次", "有效询盘 6 条", "活跃 46 天", "近期已联系"],
    },
    latest: { updated_at: "2026-09-07 09:48", content: "系统推荐卡片" },
    unread_count: 1,
    status: "unread",
    priority: "high",
    analysis: {
      intent: "客户关注冷链传感器 500 套阶梯价与 24 个月质保。",
      stage: "negotiating",
      score: 82,
      risks: ["需确认质保范围"],
      next_actions: ["确认采购数量", "发送阶梯报价"],
      summary: "Ahmed Khan 当前处于谈判中阶段，建议优先确认报价条件。",
    },
    messages: [
      { message: { external_mid: "msg-004", sid: 43, sender: 102, read: false, content: "Could you share the warranty extension options for a tiered order?", type: "text" }, created_at: "2026-09-07 09:35", role: "buyer" },
      { message: { external_mid: "msg-005", sid: 43, sender: 9001, read: true, content: "Thanks Ahmed. I will check the warranty extension policy and share a tiered quote today.", type: "text" }, created_at: "2026-09-07 09:42", role: "seller" },
      { message: { external_mid: "msg-006", sid: 43, sender: 9001, read: true, content: { card_id: "card-product-001", label: "系统推荐卡片" }, type: "card" }, created_at: "2026-09-07 09:48", role: "card" },
    ],
  },
  {
    sid: 44,
    name: "North Harbor Supplies",
    participants: [103, 9001],
    ...aggregateEntities(103),
    customer_view: {
      id: "1003",
      ali_id: "buyer-ali-003",
      name: "Maya Brown",
      company: "North Harbor Supplies",
      country: "Canada",
      email: "maya@northharbor.example",
      phone: "+1 604 000 9281",
      stage: "new",
      tags: ["展会线索", "OEM", "工业品"],
      availability: "暂未确认",
      behavior: ["商品浏览 3 次", "有效询盘 1 条", "活跃 6 天", "近期未联系"],
    },
    latest: { updated_at: "2026-09-06 22:11", content: "系统推荐卡片" },
    unread_count: 1,
    status: "unread",
    priority: "medium",
    analysis: {
      intent: "客户需要最新目录和样品条款。",
      stage: "new",
      score: 63,
      risks: ["近期未确认联系时间"],
      next_actions: ["发送产品目录", "确认样品条款"],
      summary: "Maya Brown 当前处于新线索阶段，建议先补充产品资料。",
    },
    messages: [
      { message: { external_mid: "msg-007", sid: 44, sender: 103, read: false, content: "Can you send the latest catalog and sample terms?", type: "text" }, created_at: "2026-09-06 22:00", role: "buyer" },
      { message: { external_mid: "msg-008", sid: 44, sender: 9001, read: true, content: { card_id: "card-generic-001", label: "系统推荐卡片" }, type: "card" }, created_at: "2026-09-06 22:11", role: "card" },
    ],
  },
];

export const assistantSuggestions: AssistantSuggestion[] = [
  {
    id: "sug-1",
    title: "正式报价回复",
    content: "Thanks for your interest. We can provide the sample cost, CE certificate, and lead time today. May I confirm your target quantity and preferred packaging?",
    tone: "formal",
    zh: "正式报价回复",
  },
  {
    id: "sug-2",
    title: "友好推进样品",
    content: "Happy to help. I will send the CE certificate first, then prepare a sample quote with shipping options for your review.",
    tone: "friendly",
    zh: "友好推进样品",
  },
  {
    id: "sug-3",
    title: "紧急高意向跟进",
    content: "We have sample stock available this week. If the certificate meets your requirement, I can reserve samples and arrange dispatch quickly.",
    tone: "urgent",
    zh: "紧急高意向跟进",
  },
];
