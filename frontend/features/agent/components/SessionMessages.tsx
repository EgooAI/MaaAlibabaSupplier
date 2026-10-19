"use client";

import { RobotOutlined, ShopOutlined } from "@ant-design/icons";
import { Alert, Avatar, Button, Card, Dropdown, Space, Tag, Typography } from "antd";
import { renderContentHtml } from "@/domain/render/markdown";
import { EMPTY_TEXT } from "@/components/empty";
import type { AgentTestSession } from "@/types/agent";
import { ToolCallTimeline } from "../timeline/ToolCallTimeline";

type SessionMessagesProps = {
  session: AgentTestSession;
  /** Fork/resume from one turn; omitted when there is nothing to fork. */
  onForkFromTurn?: (index: number) => void;
};

export function SessionMessages({ session, onForkFromTurn }: SessionMessagesProps) {
  if (!session.messages.length) {
    return <Typography.Text type="secondary">{EMPTY_TEXT.messages}</Typography.Text>;
  }

  return (
    <Space orientation="vertical" size="middle" className="w-full">
      {session.draft ? (
        <Alert type="warning" showIcon message="该会话使用未保存的提示词快照运行，代表当时的草稿内容。" />
      ) : null}
      {session.type === "fork" ? (
        <Alert
          type="info"
          showIcon
          message={`模拟会话：历史来自「${session.source?.conversationTitle || "真实会话"}」${
            typeof session.source?.messageIndex === "number" ? `的第 ${session.source.messageIndex + 1} 条消息` : ""
          }。后续回复不会发送给客户。`}
        />
      ) : null}
      {session.messages.map((item, index) => {
        const isAssistant = item.role === "assistant";
        const canFork = !isAssistant && Boolean(onForkFromTurn);
        return (
          <div key={item.id} className={`flex ${isAssistant ? "justify-start" : "justify-end"}`}>
            <div className={`flex max-w-[88%] gap-3 ${isAssistant ? "" : "flex-row-reverse"}`}>
              <Avatar
                className="shrink-0"
                icon={isAssistant ? <RobotOutlined /> : <ShopOutlined />}
                style={{ backgroundColor: isAssistant ? "#64748b" : "#1677ff" }}
              />
              <Card size="small" className={isAssistant ? "bg-slate-50" : "bg-blue-50"}>
                <div className="flex items-center justify-between gap-2">
                  <Typography.Text className="text-xs">{item.createdAt}</Typography.Text>
                  <Space size={4}>
                    {item.mode === "simulated" && isAssistant ? <Tag color="gold">模拟</Tag> : null}
                    {canFork ? (
                      <Dropdown
                        trigger={["click"]}
                        menu={{
                          items: [{ key: "fork", label: "从这里分叉" }],
                          onClick: () => onForkFromTurn?.(index),
                        }}
                      >
                        <Button size="small" type="text" aria-label="从这里分叉">分叉</Button>
                      </Dropdown>
                    ) : null}
                  </Space>
                </div>
                {isAssistant ? (
                  <div className="mt-2">
                    <ToolCallTimeline message={item} />
                  </div>
                ) : (
                  <div className="mt-2 break-words" dangerouslySetInnerHTML={renderContentHtml(item.content, "markdown")} />
                )}
              </Card>
            </div>
          </div>
        );
      })}
    </Space>
  );
}
