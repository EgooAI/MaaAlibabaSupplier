import { Badge, Tag } from "antd";
import type { AgentConfig } from "@/types/agent";
import type { BusinessCardStatus } from "@/types/cards";
import type { ConversationStatus } from "@/types/chatCanonical";
import type { HealthStatus, TaskStatus } from "@/types/status";

type StatusValue = ConversationStatus | BusinessCardStatus | HealthStatus | TaskStatus | AgentConfig["category"] | "enabled" | "disabled" | "high" | "medium" | "low";

const statusMap: Record<StatusValue, { label: string; color: string; badge?: "success" | "processing" | "warning" | "error" | "default" }> = {
  unread: { label: "未读", color: "red", badge: "error" },
  following: { label: "跟进中", color: "blue", badge: "processing" },
  waiting: { label: "等待客户", color: "gold", badge: "warning" },
  closed: { label: "已关闭", color: "default", badge: "default" },
  published: { label: "已发布", color: "green", badge: "success" },
  draft: { label: "草稿", color: "default", badge: "default" },
  reviewing: { label: "待审核", color: "purple", badge: "processing" },
  healthy: { label: "正常", color: "green", badge: "success" },
  warning: { label: "告警", color: "gold", badge: "warning" },
  offline: { label: "离线", color: "red", badge: "error" },
  queued: { label: "排队中", color: "default", badge: "default" },
  running: { label: "运行中", color: "blue", badge: "processing" },
  succeeded: { label: "成功", color: "green", badge: "success" },
  failed: { label: "失败", color: "red", badge: "error" },
  system: { label: "系统 Agent", color: "geekblue" },
  regular: { label: "普通 Agent", color: "cyan" },
  enabled: { label: "已启用", color: "green", badge: "success" },
  disabled: { label: "已停用", color: "default", badge: "default" },
  high: { label: "高", color: "red" },
  medium: { label: "中", color: "gold" },
  low: { label: "低", color: "green" },
};

export function StatusTag({ status, badge = false }: { status: StatusValue; badge?: boolean }) {
  const item = statusMap[status];
  if (badge) {
    return <Badge status={item.badge ?? "default"} text={item.label} />;
  }
  return <Tag color={item.color}>{item.label}</Tag>;
}
