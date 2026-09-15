/** 空态文案单源，避免各页各写各的“暂无xx”。新增文案只改这里。 */
export const EMPTY_TEXT = {
  status: "暂无状态数据",
  tasks: "暂无任务",
  conversations: "暂无会话",
  agentSessions: "暂无 Agent 会话",
  selectSession: "请选择会话或新建会话",
  llm: "暂无 LLM 配置",
  profile: "暂无个人信息",
  messages: "暂无消息",
  analysis: "当前会话暂无分析结果",
  table: "暂无数据",
} as const;

export function formatEmpty(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined) return fallback;
  const text = String(value).trim();
  return text ? String(value) : fallback;
}
