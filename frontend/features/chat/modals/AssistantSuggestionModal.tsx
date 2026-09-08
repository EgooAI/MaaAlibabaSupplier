"use client";

import { Button, Listy, Modal, Space, Tag, Typography } from "antd";
import type { AssistantSuggestion } from "@/types/chatCanonical";

export function AssistantSuggestionModal({ open, suggestions, onClose, onInsert }: { open: boolean; suggestions: AssistantSuggestion[]; onClose: () => void; onInsert: (content: string) => void }) {
  return (
    <Modal title="AI 建议回复" open={open} onCancel={onClose} footer={null} width={760}>
      <Listy
        items={suggestions}
        rowKey="id"
        virtual={false}
        itemRender={(item) => (
          <div className="flex items-start justify-between gap-4 border-b border-slate-100 py-3">
            <Space orientation="vertical" className="min-w-0 flex-1">
              <Space><Typography.Text strong>{item.title}</Typography.Text><Tag>{item.tone}</Tag></Space>
              <Typography.Paragraph className="!mb-0">{item.content}</Typography.Paragraph>
            </Space>
            <Button type="primary" onClick={() => onInsert(item.content)}>插入</Button>
          </div>
        )}
      />
    </Modal>
  );
}
