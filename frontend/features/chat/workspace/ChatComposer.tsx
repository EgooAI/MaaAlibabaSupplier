"use client";

import { MessageComposer } from "@/components/MessageComposer";
import { BulbOutlined, TranslationOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useState, type ReactNode } from "react";
import { useAccount } from "@/features/account/AccountProvider";
import { ComposeTranslator } from "./ComposeTranslator";

type ChatToolKey = "suggestions" | "intent-analysis" | "stage-analysis";

export type ChatComposerProps = {
  conversationId: string;
  value: string;
  onChange: (value: string) => void;
  onOpenSuggestions: () => void;
  suggestionAgent?: ReactNode;
  onOpenIntentAnalysis: () => void;
  onOpenStageAnalysis: () => void;
  loading: boolean;
  onSend: () => void;
  onFill?: () => void;
  sendDisabled?: boolean;
};

export function ChatComposer({
  conversationId,
  value,
  onChange,
  onOpenSuggestions,
  suggestionAgent,
  onOpenIntentAnalysis,
  onOpenStageAnalysis,
  loading,
  onSend,
  onFill,
  sendDisabled,
}: ChatComposerProps) {
  const { snapshot, blocked } = useAccount();
  const canOperate = !blocked && Boolean(snapshot?.capabilities.operate_client);
  const canUseAi = !blocked && Boolean(snapshot?.capabilities.use_ai);
  const [translateOpen, setTranslateOpen] = useState(false);
  const tools: Array<{ key: ChatToolKey; label: string }> = [
    { key: "intent-analysis", label: "客户意图分析" },
    { key: "stage-analysis", label: "客户所处阶段分析" },
  ];

  function handleToolClick(key: string) {
    if (!canUseAi) return;
    const toolKey = key as ChatToolKey;
    if (toolKey === "suggestions") onOpenSuggestions();
    if (toolKey === "intent-analysis") onOpenIntentAnalysis();
    if (toolKey === "stage-analysis") onOpenStageAnalysis();
  }

  return (
    <>
      <MessageComposer
        compact
        footer={<>
          <Button type="text" size="small" icon={<BulbOutlined />} disabled={!canUseAi} onClick={onOpenSuggestions}>AI 建议</Button>
          {suggestionAgent}
          <Button type="text" size="small" aria-label="发信翻译" aria-pressed={translateOpen} icon={<TranslationOutlined />} disabled={!canUseAi} onClick={() => setTranslateOpen((open) => !open)}>翻译</Button>
        </>}
        value={value}
        onChange={onChange}
        tools={tools.map((tool) => ({ ...tool, disabled: !canUseAi }))}
        sendDisabled={!canOperate || sendDisabled}
        onToolClick={handleToolClick}
        placeholder="输入卖家回复，或插入 AI 建议话术..."
        loading={loading}
        onSend={onSend}
        onFill={onFill}
      />
      {translateOpen ? <ComposeTranslator conversationId={conversationId} draft={value} onDraftChange={onChange} /> : null}
    </>
  );
}
