export const AGENT_TOOL_OPTIONS = [
  { label: "CRM 查询", value: "crm_query" },
  { label: "报价模板", value: "quote_template" },
  { label: "订单摘要", value: "order_summary" },
  { label: "物流计算", value: "logistics_calculator" },
] as const;

const TOOL_LABELS = new Map<string, string>(AGENT_TOOL_OPTIONS.map((option) => [option.value, option.label]));
const TOOL_NAMES = new Map<string, string>(AGENT_TOOL_OPTIONS.map((option) => [option.label, option.value]));

export function agentToolToDisplayLabel(tool: string) {
  return TOOL_LABELS.get(tool) ?? tool;
}

export function agentToolToRegisteredName(tool: string) {
  return TOOL_NAMES.get(tool) ?? tool;
}

export function normalizeAgentTools(tools: string[] = []) {
  const names = tools.map((tool) => agentToolToRegisteredName(tool.trim())).filter(Boolean);
  return Array.from(new Set(names));
}

export function displayAgentTools(tools: string[] = []) {
  return normalizeAgentTools(tools).map(agentToolToDisplayLabel);
}
