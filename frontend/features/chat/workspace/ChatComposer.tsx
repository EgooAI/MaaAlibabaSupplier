"use client";

import { Button, Modal, Typography } from "antd";
import { useState } from "react";
import { MessageComposer } from "@/components/MessageComposer";

type ChatComposerProps = {
  value: string;
  onChange: (value: string) => void;
  translationVisible: boolean;
  onToggleTranslation: () => void;
  onRetranslate: () => void;
  onOpenSuggestions: () => void;
  onOpenIntentAnalysis: () => void;
  onOpenStageAnalysis: () => void;
  loading: boolean;
  onSend: (action: "send" | "test") => void;
};

export function ChatComposer({
  value,
  onChange,
  translationVisible,
  onToggleTranslation,
  onRetranslate,
  onOpenSuggestions,
  onOpenIntentAnalysis,
  onOpenStageAnalysis,
  loading,
  onSend,
}: ChatComposerProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const tools = [
    { key: "translation", label: translationVisible ? "关闭翻译" : "翻译" },
    { key: "retranslate", label: "重新翻译" },
    { key: "suggestions", label: "AI 回复建议" },
    { key: "intent-analysis", label: "客户意图分析" },
    { key: "stage-analysis", label: "客户所处阶段分析" },
  ];

  function handleToolClick(key: string) {
    if (key === "translation") onToggleTranslation();
    if (key === "retranslate") onRetranslate();
    if (key === "suggestions") onOpenSuggestions();
    if (key === "intent-analysis") onOpenIntentAnalysis();
    if (key === "stage-analysis") onOpenStageAnalysis();
  }

  function handleConfirm(action: "send" | "test") {
    setConfirmOpen(false);
    onSend(action);
  }

  return (
    <>
      <MessageComposer
        value={value}
        onChange={onChange}
        tools={tools}
        onToolClick={handleToolClick}
        placeholder="输入卖家回复，或插入 AI 建议话术..."
        loading={loading}
        onSend={() => setConfirmOpen(true)}
      />
      <Modal
        title="确认发送"
        open={confirmOpen}
        destroyOnHidden
        onCancel={() => setConfirmOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setConfirmOpen(false)}>取消</Button>,
          <Button key="test" loading={loading} onClick={() => handleConfirm("test")}>测试（仅填入）</Button>,
          <Button key="send" type="primary" loading={loading} onClick={() => handleConfirm("send")}>确认发送</Button>,
        ]}
      >
        <Typography.Paragraph>
          测试仅将内容填入客户端输入框而不发送；确认发送将通过自动化跳转到该联系人并发送。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary" ellipsis={{ rows: 4 }}>
          {value.trim() || "（空内容）"}
        </Typography.Paragraph>
      </Modal>
    </>
  );
}
