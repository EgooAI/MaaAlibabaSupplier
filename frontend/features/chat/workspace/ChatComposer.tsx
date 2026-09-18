"use client";

import { MessageComposer } from "@/components/MessageComposer";
import { BulbOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { useAccount } from "@/features/account/AccountProvider";

type ChatToolKey = "translation" | "retranslate" | "suggestions" | "intent-analysis" | "stage-analysis";

export type ChatComposerProps = {
  value: string;
  onChange: (value: string) => void;
  translationVisible: boolean;
  onToggleTranslation: () => void;
  onRetranslate: () => void;
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
  onRetranslate,
  onOpenSuggestions,
  onOpenIntentAnalysis,
  onOpenStageAnalysis,
  loading,
  onSend,
}: ChatComposerProps) {
  const { snapshot, blocked } = useAccount();
  const canOperate = !blocked && Boolean(snapshot?.capabilities.operate_client);
  const canUseAi = !blocked && Boolean(snapshot?.capabilities.use_ai);
  const tools: Array<{ key: ChatToolKey; label: string }> = [
    { key: "translation", label: translationVisible ? "关闭翻译" : "翻译" },
    { key: "retranslate", label: "重新翻译" },
    { key: "intent-analysis", label: "客户意图分析" },
    { key: "stage-analysis", label: "客户所处阶段分析" },
  ];

  function handleToolClick(key: string) {
    if (!canUseAi && !(key === "translation" && translationVisible)) return;
    const toolKey = key as ChatToolKey;
    if (toolKey === "translation") onToggleTranslation();
    if (toolKey === "retranslate") onRetranslate();
    if (toolKey === "suggestions") onOpenSuggestions();
    if (toolKey === "intent-analysis") onOpenIntentAnalysis();
    if (toolKey === "stage-analysis") onOpenStageAnalysis();
  }

  return (
    <>
      <MessageComposer
        compact
        footer={<Button type="text" size="small" icon={<BulbOutlined />} disabled={!canUseAi} onClick={onOpenSuggestions}>AI 建议</Button>}
        value={value}
        onChange={onChange}
        tools={tools.map((tool) => ({ ...tool, disabled: !canUseAi && !(tool.key === "translation" && translationVisible) }))}
        sendDisabled={!canOperate}
        onToolClick={handleToolClick}
        placeholder="输入卖家回复，或插入 AI 建议话术..."
        loading={loading}
        onSend={onSend}
      />
    </>
  );
}
