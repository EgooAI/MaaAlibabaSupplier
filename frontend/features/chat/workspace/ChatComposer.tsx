"use client";

import { MessageComposer } from "@/components/MessageComposer";

type ChatComposerProps = {
  value: string;
  onChange: (value: string) => void;
  translationVisible: boolean;
  onToggleTranslation: () => void;
  onOpenSuggestions: () => void;
  onOpenIntentAnalysis: () => void;
  onOpenStageAnalysis: () => void;
  loading: boolean;
  onSend: () => void;
};

export function ChatComposer({
  value,
  onChange,
  translationVisible,
  onToggleTranslation,
  onOpenSuggestions,
  onOpenIntentAnalysis,
  onOpenStageAnalysis,
  loading,
  onSend,
}: ChatComposerProps) {
  const tools = [
    { key: "translation", label: translationVisible ? "关闭翻译" : "翻译" },
    { key: "suggestions", label: "AI 回复建议" },
    { key: "intent-analysis", label: "客户意图分析" },
    { key: "stage-analysis", label: "客户所处阶段分析" },
  ];

  function handleToolClick(key: string) {
    if (key === "translation") onToggleTranslation();
    if (key === "suggestions") onOpenSuggestions();
    if (key === "intent-analysis") onOpenIntentAnalysis();
    if (key === "stage-analysis") onOpenStageAnalysis();
  }

  return (
    <MessageComposer
      value={value}
      onChange={onChange}
      tools={tools}
      onToolClick={handleToolClick}
      placeholder="输入卖家回复，或插入 AI 建议话术..."
      loading={loading}
      onSend={onSend}
    />
  );
}
