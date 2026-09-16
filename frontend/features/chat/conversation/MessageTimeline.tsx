"use client";

import { InfoCircleOutlined, RobotOutlined, ShopOutlined } from "@ant-design/icons";
import { Avatar, Button, Card, Space, Typography } from "antd";
import { BusinessCardView } from "@/components/BusinessCardView";
import { avatarColorOf, avatarInitialOf } from "@/domain/chat/avatarModel";
import { renderMessageHtml } from "@/domain/chat/messageHtml";
import type { ChatMessage } from "@/types/chatCanonical";

export function MessageTimeline({ messages, buyerId = "buyer", buyerName = "", showTranslations = true, onRegenerate, onOpenCard }: { messages: ChatMessage[]; buyerId?: string; buyerName?: string; showTranslations?: boolean; onRegenerate: (message: ChatMessage) => void; onOpenCard: (cardId: string) => void }) {
  return (
    <Space orientation="vertical" className="w-full" size="middle">
      {messages.map((message) => {
        const isSeller = message.role === "seller";
        const isSystem = message.role === "system" || message.role === "unknown";
        const card = message.card;
        return (
          <div key={message.id} className={`flex ${isSeller ? "justify-end" : "justify-start"}`}>
            <div className={`flex max-w-[78%] gap-3 ${isSeller ? "flex-row-reverse" : ""}`}>
              {isSeller ? (
                <Avatar icon={<ShopOutlined />} style={{ backgroundColor: "#1677ff" }} />
              ) : isSystem ? (
                <Avatar icon={<InfoCircleOutlined />} style={{ backgroundColor: "#b45309" }} />
              ) : message.role === "card" ? (
                <Avatar icon={<RobotOutlined />} style={{ backgroundColor: "#64748b" }} />
              ) : (
                <Avatar style={{ backgroundColor: avatarColorOf(buyerId) }}>{avatarInitialOf(buyerName)}</Avatar>
              )}
              <Card size="small" className={isSeller ? "bg-blue-50" : isSystem ? "bg-slate-50" : "bg-white"}>
                <Space orientation="vertical" size={8} className="w-full">
                  <Typography.Text className="text-xs">{message.createdAt}</Typography.Text>
                  {card ? (
                    <BusinessCardView card={card} compact onClick={() => onOpenCard(card.id)} />
                  ) : (
                    <div className="whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderMessageHtml(message.content)} />
                  )}
                  {showTranslations && message.translatedContent ? (
                    <div className="rounded-lg bg-white/70 p-2 text-slate-600">
                      <Typography.Text className="text-xs" type="secondary">译文</Typography.Text>
                      <div className="whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderMessageHtml(message.translatedContent)} />
                    </div>
                  ) : null}
                  {showTranslations && message.role === "buyer" && message.translatedContent ? (
                    <Button size="small" type="link" onClick={() => onRegenerate(message)}>重新翻译</Button>
                  ) : null}
                </Space>
              </Card>
            </div>
          </div>
        );
      })}
    </Space>
  );
}
