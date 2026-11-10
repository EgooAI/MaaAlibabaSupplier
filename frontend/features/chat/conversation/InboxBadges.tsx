import { Space, Tag } from "antd";
import { statusLabel, waitingLabel } from "@/domain/chat/chatModel";
import type { Conversation } from "@/types/chatCanonical";

export function InboxBadges({ conversation, compact = false }: { conversation: Conversation; compact?: boolean }) {
  const waiting = waitingLabel(conversation);
  const routineStatus = conversation.replyState === "waiting_customer" || conversation.replyState === "none";
  return <Space size={2} wrap>
    {!compact || conversation.unreadCount > 0 ? <Tag color={conversation.unreadCount ? "blue" : "default"}>本工作台未读 {conversation.unreadCount}</Tag> : null}
    {routineStatus ? null : (
      <Tag color={conversation.replyState === "needs_reply" ? "orange" : "default"}>{statusLabel(conversation.replyState)}</Tag>
    )}
    {waiting ? <Tag color="orange">{waiting}</Tag> : null}
    {conversation.uncertain ? <Tag>状态待核实</Tag> : null}
  </Space>;
}
