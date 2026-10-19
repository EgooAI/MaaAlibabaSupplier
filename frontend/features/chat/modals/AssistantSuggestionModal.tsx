"use client";

import { Alert, Button, List, Modal, Space, Tag, Typography } from "antd";
import type { AssistantSuggestion } from "@/types/chatCanonical";

const TONE_LABELS: Record<string, string> = {
  formal: "正式",
  friendly: "友好",
  urgent: "催办",
};

export function AssistantSuggestionModal({
  open,
  suggestions,
  reasoning,
  restricted,
  error,
  onClose,
  onInsert,
}: {
  open: boolean;
  suggestions: AssistantSuggestion[];
  /** The Agent's reasoning; replies live in ``suggestions``. */
  reasoning?: string;
  restricted?: boolean;
  error?: string;
  onClose: () => void;
  onInsert: (item: AssistantSuggestion) => void;
}) {
  return (
    <Modal title="AI 建议回复" open={open} onCancel={onClose} footer={null} width={760}>
      {error ? <Alert type="warning" showIcon title={error} /> : null}
      {restricted ? <Alert type="warning" showIcon title="工具调用达到上限，结果可能不完整。" className="mb-2" /> : null}
      {reasoning?.trim() ? (
        <div className="mb-3 rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-600">
          <Typography.Text type="secondary" className="mr-1 text-xs">判断理由</Typography.Text>
          <span className="whitespace-pre-wrap break-words">{reasoning.trim()}</span>
        </div>
      ) : null}
      <List
        dataSource={suggestions}
        locale={{ emptyText: "暂无建议" }}
        rowKey={(item) => String(item.intentId ?? item.id)}
        renderItem={(item) => (
          <List.Item
            className="!px-0"
            actions={[
              <Button key="insert" type="primary" onClick={() => onInsert(item)}>插入</Button>,
            ]}
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <Space wrap>
                <Typography.Text strong>{item.title}</Typography.Text>
                <Tag>{TONE_LABELS[item.tone] ?? item.tone}</Tag>
                {item.status && item.status !== "requested" ? <Tag color="default">{item.status}</Tag> : null}
              </Space>
              <Typography.Paragraph className="!mb-0 whitespace-pre-wrap break-words">{item.content}</Typography.Paragraph>
              {item.zh && item.zh !== item.content ? (
                <Typography.Text type="secondary" className="text-xs">{item.zh}</Typography.Text>
              ) : null}
            </div>
          </List.Item>
        )}
      />
    </Modal>
  );
}
