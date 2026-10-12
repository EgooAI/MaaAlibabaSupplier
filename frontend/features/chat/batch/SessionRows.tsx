"use client";

import { conversationTimeLabel } from "@/domain/chat/chatModel";
import { InboxBadges } from "../conversation/InboxBadges";
import type { Conversation } from "@/types/chatCanonical";

/** 客户行展开后的会话子行（表格与详情抽屉共用）。 */
export function SessionRows({ conversations }: { conversations: Conversation[] }) {
  return (
    <div className="rounded-md border border-slate-100 bg-slate-50/70">
      {conversations.map((conversation) => (
        <div key={conversation.id} className="flex items-center gap-3 border-b border-slate-100 px-3 py-1.5 text-xs last:border-b-0">
          <span className="shrink-0 font-mono text-slate-500">会话 #{conversation.id}</span>
          <InboxBadges conversation={conversation} />
          <span className="shrink-0 text-slate-400">{conversationTimeLabel(conversation.updatedAt)}</span>
          <span className="min-w-0 flex-1 truncate text-slate-500" title={conversation.latestMessage}>{conversation.latestMessage || "—"}</span>
        </div>
      ))}
    </div>
  );
}
