"use client";

import { InfoCircleOutlined, RobotOutlined, ShopOutlined } from "@ant-design/icons";
import { Avatar, Button } from "antd";
import { BusinessCardView } from "@/components/BusinessCardView";
import { avatarColorOf, avatarInitialOf } from "@/domain/chat/avatarModel";
import { renderMessageHtml } from "@/domain/chat/messageHtml";
import type { ChatMessage } from "@/types/chatCanonical";

export function MessageTimeline({ messages, buyerId = "buyer", buyerName = "", showTranslations = true, onRegenerate, onOpenCard }: { messages: ChatMessage[]; buyerId?: string; buyerName?: string; showTranslations?: boolean; onRegenerate?: (message: ChatMessage) => void; onOpenCard: (cardId: string) => void }) {
  return (
    <div className="flex w-full flex-col gap-5">
      {messages.map((message) => {
        const isSeller = message.role === "seller";
        const isSystem = message.role === "system" || message.role === "unknown";
        const card = message.card;
        return (
          <div key={message.id} className={`flex ${isSeller ? "justify-end" : "justify-start"}`}>
            <div className={`flex min-w-0 max-w-full items-start gap-2 sm:max-w-[88%] sm:gap-3 ${isSeller ? "flex-row-reverse" : ""}`}>
              <div className="hidden shrink-0 sm:block">
              {isSeller ? (
                <Avatar icon={<ShopOutlined />} style={{ backgroundColor: "#1677ff" }} />
              ) : isSystem ? (
                <Avatar icon={<InfoCircleOutlined />} style={{ backgroundColor: "#b45309" }} />
              ) : message.role === "card" ? (
                <Avatar icon={<RobotOutlined />} style={{ backgroundColor: "#64748b" }} />
              ) : (
                <Avatar style={{ backgroundColor: avatarColorOf(buyerId) }}>{avatarInitialOf(buyerName)}</Avatar>
              )}
              </div>
              <div className="min-w-0">
                <div className={`mb-1.5 text-[11px] text-slate-400 ${isSeller ? "text-right" : ""}`}>{message.createdAt}</div>
                <div className={`rounded-2xl px-3 py-2.5 text-sm leading-relaxed [overflow-wrap:anywhere] sm:px-4 ${isSeller ? "rounded-tr-sm bg-blue-50" : isSystem ? "bg-slate-100 text-slate-600" : "rounded-tl-sm border border-slate-100 bg-white"}`}>
                  {card ? (
                    <BusinessCardView card={card} compact onClick={() => onOpenCard(card.id)} />
                  ) : (
                    <div className="whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderMessageHtml(message.content)} />
                  )}
                  {showTranslations && message.translatedContent ? (
                    <div className="mt-3 border-t border-slate-200/70 pt-2 text-slate-500">
                      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                        <span>译文</span>
                        {message.role === "buyer" ? <Button size="small" type="text" disabled={!onRegenerate} onClick={() => onRegenerate?.(message)}>重新翻译</Button> : null}
                      </div>
                      <div className="whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderMessageHtml(message.translatedContent)} />
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
