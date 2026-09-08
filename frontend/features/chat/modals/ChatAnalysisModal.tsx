"use client";

import { Listy, Modal, Progress, Space, Spin, Tag, Typography } from "antd";
import { stageLabel } from "@/domain/chat/chatModel";
import type { ConversationAnalysis } from "@/types/chatCanonical";

type AnalysisFocus = "intent" | "stage";

type ChatAnalysisModalProps = {
  open: boolean;
  analysis?: ConversationAnalysis;
  focus?: AnalysisFocus;
  loading?: boolean;
  error?: string;
  onClose: () => void;
};

const focusConfig: Record<AnalysisFocus, {
  title: string;
  primaryLabel: string;
  primaryColor: string;
  value: (analysis: ConversationAnalysis) => string;
  riskLabel: string;
  actionLabel: string;
}> = {
  intent: {
    title: "客户意图分析",
    primaryLabel: "当前客户意图",
    primaryColor: "blue",
    value: (analysis) => analysis.intent,
    riskLabel: "意图相关待关注事项",
    actionLabel: "意图跟进动作",
  },
  stage: {
    title: "客户所处阶段分析",
    primaryLabel: "当前客户阶段",
    primaryColor: "purple",
    value: (analysis) => stageLabel(analysis.stage),
    riskLabel: "阶段相关待关注事项",
    actionLabel: "阶段推进动作",
  },
};

export function ChatAnalysisModal({ open, analysis, focus = "intent", loading = false, error, onClose }: ChatAnalysisModalProps) {
  const config = focusConfig[focus];

  return (
    <Modal title={config.title} open={open} onCancel={onClose} footer={null} width={680}>
      {loading ? <div className="flex justify-center py-10"><Spin /></div> : null}
      {!loading && error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
      {!loading && !error && !analysis ? <Typography.Text>当前会话暂无分析结果</Typography.Text> : null}
      {!loading && !error && analysis ? (
        <Space orientation="vertical" className="w-full">
          <Typography.Text strong>{config.primaryLabel}</Typography.Text>
          <Tag color={config.primaryColor}>{config.value(analysis)}</Tag>
          <Typography.Text strong>评分</Typography.Text>
          <Progress percent={analysis.score} status={analysis.score > 80 ? "success" : "active"} />
          <Typography.Text strong>{config.riskLabel}</Typography.Text>
          <AnalysisList items={analysis.risks} />
          <Typography.Text strong>{config.actionLabel}</Typography.Text>
          <AnalysisList items={analysis.nextActions} />
        </Space>
      ) : null}
    </Modal>
  );
}

function AnalysisList({ items }: { items: string[] }) {
  return (
    <Listy
      items={items.map((text, index) => ({ id: `${index}-${text}`, text }))}
      rowKey="id"
      virtual={false}
      itemRender={(item) => <div className="py-2">{item.text}</div>}
    />
  );
}
