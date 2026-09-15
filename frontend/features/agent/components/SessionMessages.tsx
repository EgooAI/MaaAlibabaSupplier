"use client";

import { RobotOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Card, Space, Typography } from "antd";
import { useState } from "react";
import { fallbackAvatarUrl, sellerAvatarUrl } from "@/domain/chat/avatarModel";
import type { AgentTestSession } from "@/types/agent";

export function SessionMessages({ session }: { session: AgentTestSession }) {
  const [userAvatar, setUserAvatar] = useState(sellerAvatarUrl);

  if (!session.messages.length) {
    return <Typography.Text type="secondary">暂无消息</Typography.Text>;
  }

  return (
    <Space orientation="vertical" size="middle" className="w-full">
      {session.messages.map((item) => {
        const isAssistant = item.role === "assistant";
        return (
          <div key={item.id} className={`flex ${isAssistant ? "justify-start" : "justify-end"}`}>
            <div className={`flex max-w-[78%] gap-3 ${isAssistant ? "" : "flex-row-reverse"}`}>
              <Avatar
                className="shrink-0"
                src={isAssistant ? undefined : userAvatar}
                icon={isAssistant ? <RobotOutlined /> : <UserOutlined />}
                style={{ backgroundColor: isAssistant ? "#64748b" : "#e2e8f0" }}
                onError={isAssistant ? undefined : () => {
                  setUserAvatar(fallbackAvatarUrl);
                  return true;
                }}
              />
              <Card size="small" className={isAssistant ? "bg-slate-50" : "bg-blue-50"}>
                <Typography.Text className="text-xs">{item.createdAt}</Typography.Text>
                <Typography.Paragraph className="!mb-0 mt-2 whitespace-pre-wrap">{item.content}</Typography.Paragraph>
              </Card>
            </div>
          </div>
        );
      })}
    </Space>
  );
}
