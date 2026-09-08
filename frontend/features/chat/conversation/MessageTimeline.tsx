"use client";

import { RobotOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Button, Card, Space, Typography } from "antd";
import { BusinessCardView } from "@/components/BusinessCardView";
import { fallbackAvatarUrl, sellerAvatarUrl, conversationAvatarUrl } from "@/domain/chat/avatarModel";
import type { ChatMessage } from "@/types/chatCanonical";
import { useState } from "react";

export function MessageTimeline({ messages, buyerId = "buyer", showTranslations = true, onRegenerate, onOpenCard }: { messages: ChatMessage[]; buyerId?: string; showTranslations?: boolean; onRegenerate: (message: ChatMessage) => void; onOpenCard: (cardId: string) => void }) {
  const buyerAvatar = conversationAvatarUrl(buyerId);
  const [sellerAvatar, setSellerAvatar] = useState(sellerAvatarUrl);

  return (
    <Space orientation="vertical" className="w-full" size="middle">
      {messages.map((message) => {
        const isSeller = message.role === "seller";
        const isSystem = message.role === "system" || message.role === "unknown";
        const isBot = isSystem || message.role === "card";
        const card = message.card;
        return (
          <div key={message.id} className={`flex ${isSeller ? "justify-end" : "justify-start"}`}>
            <div className={`flex max-w-[78%] gap-3 ${isSeller ? "flex-row-reverse" : ""}`}>
              <Avatar
                src={isBot ? undefined : isSeller ? sellerAvatar : buyerAvatar}
                icon={isBot ? <RobotOutlined /> : <UserOutlined />}
                style={{ backgroundColor: isSeller ? "#e2e8f0" : isBot ? "#64748b" : "#10b981" }}
                onError={isSeller ? () => {
                  setSellerAvatar(fallbackAvatarUrl);
                  return true;
                } : undefined}
              />
              <Card size="small" className={isSeller ? "bg-blue-50" : isSystem ? "bg-slate-50" : "bg-white"}>
                <Space orientation="vertical" size={8} className="w-full">
                  <Typography.Text className="text-xs">{message.createdAt}</Typography.Text>
                  {card ? (
                    <BusinessCardView card={card} compact onClick={() => onOpenCard(card.id)} />
                  ) : (
                    <Typography.Paragraph className="!mb-0 whitespace-pre-wrap">{message.content}</Typography.Paragraph>
                  )}
                  {showTranslations && message.translatedContent ? (
                    <Typography.Paragraph className="!mb-0 rounded-lg bg-white/70 p-2 text-slate-600">译文：{message.translatedContent}</Typography.Paragraph>
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
