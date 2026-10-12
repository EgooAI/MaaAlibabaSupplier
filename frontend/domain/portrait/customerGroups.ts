import type { Conversation } from "@/types/chatCanonical";

export const UNRESOLVED_GROUP_KEY = "na";

export interface CustomerGroup {
  /** 稳定行键：`c:<cid>`；cid 缺失的会话合并为 `na`。 */
  key: string;
  cid: number | null;
  conversations: Conversation[];
  sessionIds: string[];
  unread: number;
  needsReply: number;
  /** 排序中最新的一条会话（列表本身按时间倒序）。 */
  representative: Conversation;
}

export function customerLabelOf(group: CustomerGroup): string {
  return group.cid === null ? "未识别客户" : group.representative.customer.name;
}

/** 将当前页会话按 cid 分组；cid 缺失的会话合并为一个伪客户行并排在末尾。 */
export function groupCustomers(conversations: Conversation[]): CustomerGroup[] {
  const groups: CustomerGroup[] = [];
  const byCid = new Map<number, CustomerGroup>();
  const unresolved: Conversation[] = [];
  for (const conversation of conversations) {
    const cid = conversation.customer.cid;
    if (cid === null) {
      unresolved.push(conversation);
      continue;
    }
    let group = byCid.get(cid);
    if (!group) {
      group = {
        key: `c:${cid}`,
        cid,
        conversations: [],
        sessionIds: [],
        unread: 0,
        needsReply: 0,
        representative: conversation,
      };
      byCid.set(cid, group);
      groups.push(group);
    }
    group.conversations.push(conversation);
    group.sessionIds.push(conversation.id);
    group.unread += conversation.unreadCount;
    if (conversation.replyState === "needs_reply") group.needsReply += 1;
  }
  if (unresolved.length) {
    groups.push({
      key: UNRESOLVED_GROUP_KEY,
      cid: null,
      conversations: unresolved,
      sessionIds: unresolved.map((conversation) => conversation.id),
      unread: unresolved.reduce((sum, conversation) => sum + conversation.unreadCount, 0),
      needsReply: unresolved.filter((conversation) => conversation.replyState === "needs_reply").length,
      representative: unresolved[0],
    });
  }
  return groups;
}
