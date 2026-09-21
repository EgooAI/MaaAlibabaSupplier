"use client";

import { RobotOutlined, ShopOutlined } from "@ant-design/icons";
import { Avatar, Card, Space, Typography } from "antd";
import { renderMessageHtml } from "@/domain/chat/messageHtml";
import { EMPTY_TEXT } from "@/components/empty";
import type { AgentTestSession } from "@/types/agent";

export function SessionMessages({ session }: { session: AgentTestSession }) {
  if (!session.messages.length) {
    return <Typography.Text type="secondary">{EMPTY_TEXT.messages}</Typography.Text>;
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
                icon={isAssistant ? <RobotOutlined /> : <ShopOutlined />}
                style={{ backgroundColor: isAssistant ? "#64748b" : "#1677ff" }}
              />
              <Card size="small" className={isAssistant ? "bg-slate-50" : "bg-blue-50"}>
                <Typography.Text className="text-xs">{item.createdAt}</Typography.Text>
                <div className="mt-2 whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderMessageHtml(item.content)} />
              </Card>
            </div>
          </div>
        );
      })}
    </Space>
  );
}
