"use client";

import { RobotOutlined, ShopOutlined } from "@ant-design/icons";
import { Alert, Avatar, Card, Space, Typography } from "antd";
import { renderContentHtml } from "@/domain/render/markdown";
import { EMPTY_TEXT } from "@/components/empty";
import type { AgentTestSession } from "@/types/agent";

export function SessionMessages({ session }: { session: AgentTestSession }) {
  if (!session.messages.length) {
    return <Typography.Text type="secondary">{EMPTY_TEXT.messages}</Typography.Text>;
  }

  return (
    <Space orientation="vertical" size="middle" className="w-full">
      {session.draft ? (
        <Alert type="warning" showIcon message="该会话使用未保存的提示词快照运行，代表当时的草稿内容。" />
      ) : null}
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
                <div className="mt-2 break-words" dangerouslySetInnerHTML={renderContentHtml(item.content, "markdown")} />
              </Card>
            </div>
          </div>
        );
      })}
    </Space>
  );
}
