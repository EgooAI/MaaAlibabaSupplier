import { SYSTEM_AGENT_APIDS, agentPresetToConfig } from "@/domain/agent/agentModel";
import type { AgentConsoleState, AgentPreset, DocumentLlmConfig, LlmLevelConfig, SystemAgentDefinition } from "@/types/agent";

const systemAgentSources = [
  {
    apid: SYSTEM_AGENT_APIDS.translation,
    name: "翻译",
    description: "翻译",
    prompt: `你是聊天消息翻译助手。

请把用户输入 JSON 中每个 items[].text 翻译成简体中文。

输出要求：
1) 只输出 JSON，不要输出解释性文字。
2) 输出格式必须是：{"translations": {"<text_hash>": "<translation or null>"}}。
3) 如果某条文本已经是简体中文，对应 text_hash 返回 null。
4) 不要遗漏任何 text_hash。
5) 不要编造原文不存在的信息。`,
    level: 0,
    tools: [],
  },
  {
    apid: SYSTEM_AGENT_APIDS.replySuggestion,
    name: "建议",
    description: "建议",
    prompt: `你是一名阿里巴巴国际站供应商客服，正在处理买家的询盘对话。
请根据【对话记录】生成可直接发送给买家的回复建议。

输出要求：
1) 只输出 JSON，字段见 schema。
2) 为每条建议同时给出中文 zh 和买家语言 content。
3) content 必须使用买家在对话中使用的语言，不要默认翻译为英文。
4) 最多给出 3 条建议，按推荐顺序排列。
5) 语气专业、友好、简洁，优先推进成交。
6) 不要编造任何无法从对话中确定的信息；信息不足时用提问补齐。
7) 不要提及你是 AI，也不要输出解释性文字。
8) tone 只能使用 formal、friendly、urgent 之一。

只输出 JSON 数组，每项必须包含 id、title、content、tone、zh 字段，例如：
[{"id":"sug-1","title":"正式报价回复","content":"buyer language reply","tone":"formal","zh":"中文建议"}]
不要输出未定义字段或顶层对象。`,
    level: 0,
    tools: [],
  },
  {
    apid: SYSTEM_AGENT_APIDS.intentAnalysis,
    name: "客户意图分析",
    description: "客户意图分析",
    prompt: `你是客户意图分析助手。

请基于用户提供的【任务】和【聊天记录】分析客户采购意图、关注点和下一步动作。结论必须来自聊天内容，不要编造未出现的信息。

输出要求：
1) 输出中文。
2) 结构清晰，重点给出可执行建议。
3) 优先输出 JSON：{"intent": "客户意图", "evidence": ["依据"], "concerns": ["顾虑"], "next_actions": ["下一步建议"]}。
4) 如果信息不足，请明确说明缺少哪些判断依据。`,
    level: 0,
    tools: [],
  },
  {
    apid: SYSTEM_AGENT_APIDS.stageAnalysis,
    name: "客户所处阶段分析",
    description: "客户所处阶段分析",
    prompt: `你是客户阶段分析助手。

请基于用户提供的【任务】和【聊天记录】分析客户当前所处阶段。结论必须来自聊天内容，不要编造未出现的信息。

输出要求：
1) 输出中文。
2) 结构清晰，重点给出可执行建议。
3) 优先输出 JSON：{"stage": "客户阶段", "evidence": ["依据"], "next_actions": ["下一步建议"], "confidence": "置信度"}。
4) 如果信息不足，请明确说明缺少哪些判断依据。`,
    level: 0,
    tools: [],
  },
] as const;

const regularAgentSources = [
  {
    apid: "agent-quote",
    name: "报价跟进助手",
    description: "根据产品、MOQ 和客户历史生成报价跟进话术。",
    prompt: "你是报价跟进助手，需要用英文生成清晰、专业、可直接发送的回复。",
    level: 2,
    tools: ["crm_query", "quote_template"],
    updatedAt: "2026-08-11T09:25:00+08:00",
  },
  {
    apid: "agent-risk",
    name: "订单风险审阅",
    description: "审阅客户需求中的履约、价格、付款风险。",
    prompt: "识别潜在订单风险并给出卖家可执行建议。",
    level: 3,
    tools: ["crm_query", "order_summary", "logistics_calculator"],
    updatedAt: "2026-08-11T09:25:00+08:00",
  },
] as const;

const defaultUpdatedAt = "2026-09-09T00:00:00+08:00";

export const llmLevels: LlmLevelConfig[] = Array.from({ length: 5 }, (_, level) => ({
  level,
  baseUrl: "https://api.mock-llm.example/v1",
  apiKey: `sk-mock-level-${level}-placeholder`,
  modelName: level >= 3 ? "claude-sonnet-5" : "claude-haiku-4-5-20251001",
  systemPrompt: `Level ${level} agent prompt for Alibaba seller workflow.`,
  context: 12000 + level * 4000,
  maxToolRounds: 3 + level,
}));

const llmLevel = llmLevels[3];

export const documentLlmConfig: DocumentLlmConfig = {
  level: llmLevel.level,
  base_url: llmLevel.baseUrl,
  api_key: llmLevel.apiKey,
  model_name: llmLevel.modelName,
  system_prompt: llmLevel.systemPrompt,
  context: llmLevel.context,
  max_tool_rounds: llmLevel.maxToolRounds,
};

export const systemAgents: SystemAgentDefinition[] = systemAgentSources.map((agent) => ({
  display_name: agent.name,
  apid: agent.apid,
  description: agent.description,
}));

export const agentPresets: AgentPreset[] = [
  ...systemAgentSources.map((agent) => ({
    id: agent.apid,
    name: agent.name,
    category: "system" as const,
    enabled: true,
    description: agent.description,
    prompt: agent.prompt,
    level: agent.level,
    tools: [...agent.tools],
    updated_at: defaultUpdatedAt,
  })),
  ...regularAgentSources.map((agent) => ({
    id: agent.apid,
    name: agent.name,
    category: "regular" as const,
    enabled: true,
    description: agent.description,
    prompt: agent.prompt,
    level: agent.level,
    tools: [...agent.tools],
    updated_at: agent.updatedAt,
  })),
];

export const agentConsole: AgentConsoleState = {
  llmLevels,
  agents: agentPresets.map(agentPresetToConfig),
  agentPresets,
  systemAgents,
  history: [
    {
      id: "hist-001",
      title: "Nordic Home 彩盒报价",
      agentId: "agent-quote",
      createdAt: "2026-08-11T09:25:00+08:00",
      messages: [
        { id: "h1-m1", role: "user", content: "客户询问彩盒成本和交期，如何回复？", createdAt: "2026-08-11T09:23:00+08:00" },
      ],
    },
    {
      id: "hist-002",
      title: "GreenMart MOQ 跟进",
      agentId: "agent-quote",
      createdAt: "2026-09-08T14:36:00+08:00",
      messages: [
        { id: "h2-m1", role: "user", content: "客户认为 500 件的 MOQ 太高，请给一条兼顾利润和成交率的英文回复。", createdAt: "2026-09-08T14:36:00+08:00" },
        { id: "h2-m2", role: "assistant", content: "Thank you for your feedback. We can offer a trial order of 300 units at a slightly adjusted unit price, and apply the standard price once the order reaches 500 units.", createdAt: "2026-09-08T14:36:08+08:00" },
      ],
    },
    {
      id: "hist-003",
      title: "Ocean Retail 付款风险审阅",
      agentId: "agent-risk",
      createdAt: "2026-09-09T10:18:00+08:00",
      messages: [
        { id: "h3-m1", role: "user", content: "客户要求先发货、收货后 60 天付款，请评估风险并给出建议。", createdAt: "2026-09-09T10:18:00+08:00" },
        { id: "h3-m2", role: "assistant", content: "该账期会显著增加回款与拒付风险。建议先核验客户资信，并采用 30% 预付款、余款见提单副本支付；若必须提供账期，应配置出口信用保险和明确的授信额度。", createdAt: "2026-09-09T10:18:09+08:00" },
      ],
    },
  ],
};
