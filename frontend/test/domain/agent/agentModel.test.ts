import { describe, expect, it } from "vitest";
import { AGENT_TOOL_OPTIONS, SYSTEM_AGENT_APIDS, agentCategoryLabel, agentPresetToConfig, agentPresetToDbPreset, canRegenerateAgentTestReply, canRunAgentExecution, canUndoAgentTestTurn, createRegularAgentPreset, dbPresetToAgentPreset, displayAgentTools, documentToLlmLevelConfig, filterAgentTestSessionsByCategory, filterAgentsByCategory, formatAgentSessionDate, isValidToolRoundLimit, llmLevelToDocumentConfig, nextAgentTestSessionId, normalizeAgentEditValues, normalizeAgentLevel, normalizeAgentTools, removeLatestAgentTestTurn, replaceLatestAgentTestReply } from "@/domain/agent/agentModel";
import { agentPresets } from "@/mock/agentData";
import type { AgentConfig, AgentPreset, AgentTestSession, DocumentLlmConfig } from "@/types/agent";

const agents: AgentConfig[] = [
  { id: "regular-1", name: "普通 Agent 1", category: "regular", enabled: true, capabilities: [], description: "", updatedAt: "" },
  { id: "system-1", name: "系统 Agent 1", category: "system", enabled: true, capabilities: [], description: "", updatedAt: "" },
  { id: "regular-2", name: "普通 Agent 2", category: "regular", enabled: false, capabilities: [], description: "", updatedAt: "" },
];

const sessions: AgentTestSession[] = [
  { id: "session-system", title: "系统测试", agentId: "system-1", createdAt: "", messages: [] },
  { id: "session-regular", title: "普通测试", agentId: "regular-1", createdAt: "", messages: [] },
];

describe("agent model", () => {
  it("returns category labels", () => {
    expect(agentCategoryLabel("system")).toBe("系统 Agent");
    expect(agentCategoryLabel("regular")).toBe("普通 Agent");
  });

  it("filters agents by category while preserving order", () => {
    expect(filterAgentsByCategory(agents, "system").map((agent) => agent.id)).toEqual(["system-1"]);
    expect(filterAgentsByCategory(agents, "regular").map((agent) => agent.id)).toEqual(["regular-1", "regular-2"]);
  });

  it("filters test sessions by the agents in a category", () => {
    expect(filterAgentTestSessionsByCategory(sessions, agents, "system").map((session) => session.id)).toEqual(["session-system"]);
    expect(filterAgentTestSessionsByCategory(sessions, agents, "regular").map((session) => session.id)).toEqual(["session-regular"]);
    expect(filterAgentTestSessionsByCategory(sessions, [], "system")).toEqual([]);
  });

  it("formats session dates without the year, seconds, or timezone", () => {
    expect(formatAgentSessionDate("2026-09-07T10:21:00+08:00")).toBe("09-07 10:21");
  });

  it("identifies and removes the latest complete turn", () => {
    const session = {
      id: "session-1",
      title: "测试",
      agentId: "regular-1",
      createdAt: "",
      messages: [
        { id: "u1", role: "user" as const, content: "第一问", createdAt: "" },
        { id: "a1", role: "assistant" as const, content: "第一答", createdAt: "" },
        { id: "u2", role: "user" as const, content: "第二问", createdAt: "" },
        { id: "a2", role: "assistant" as const, content: "第二答", createdAt: "" },
      ],
    };
    expect(canUndoAgentTestTurn(session)).toBe(true);
    expect(canRegenerateAgentTestReply(session)).toBe(true);
    expect(removeLatestAgentTestTurn(session)?.messages.map((item) => item.id)).toEqual(["u1", "a1"]);
    expect(replaceLatestAgentTestReply(session, { id: "a3", role: "assistant", content: "新答", createdAt: "" })?.messages.map((item) => item.id)).toEqual(["u1", "a1", "u2", "a3"]);
  });

  it("does not operate on an incomplete latest turn", () => {
    const session = { ...sessions[1], messages: [{ id: "u1", role: "user" as const, content: "未完成", createdAt: "" }] };
    expect(canUndoAgentTestTurn(session)).toBe(false);
    expect(canRegenerateAgentTestReply(session)).toBe(false);
    expect(removeLatestAgentTestTurn(session)).toBeNull();
  });

  it("derives the next active session id after deletion", () => {
    const source = [
      { ...sessions[0], id: "session-a" },
      { ...sessions[0], id: "session-b" },
      { ...sessions[0], id: "session-c" },
    ];
    expect(nextAgentTestSessionId(source, "session-b")).toBe("session-a");
    expect(nextAgentTestSessionId(source, "session-a")).toBe("session-b");
    expect(nextAgentTestSessionId([{ ...sessions[0], id: "only" }], "only")).toBeUndefined();
  });

  it("exposes one registered tool option source and ignores object prototype keys", () => {
    expect(AGENT_TOOL_OPTIONS.map((option) => option.value)).toEqual(["crm_query", "quote_template", "order_summary", "logistics_calculator"]);
    expect(displayAgentTools(["toString", "CRM 查询"])).toEqual(["toString", "CRM 查询"]);
    expect(normalizeAgentTools(["CRM 查询", "crm_query", "  "])).toEqual(["crm_query"]);
  });

  it("normalizes edit values before building presets", () => {
    expect(() => normalizeAgentEditValues({ name: " 报价 ", description: undefined, prompt: " prompt ", level: 2.5, capabilities: [] })).toThrow("Agent 等级必须是 0 到 4 的整数");
    expect(() => normalizeAgentEditValues({ name: " ", prompt: "prompt", level: 0 })).toThrow("请输入名称");
    expect(createRegularAgentPreset({ name: " 报价 ", prompt: " prompt ", level: 2, capabilities: ["CRM 查询"] }, "2026-09-11", "agent-new")).toMatchObject({ name: "报价", description: "", prompt: "prompt", level: 2, tools: ["crm_query"] });
  });

  it("checks agent execution availability from enabled state", () => {
    expect(canRunAgentExecution(agents[0])).toBe(true);
    expect(canRunAgentExecution(agents[2])).toBe(false);
    expect(canRunAgentExecution(undefined)).toBe(false);
  });

  it("maps the domain preset to the database DTO at the service boundary", () => {
    const preset: AgentPreset = {
      id: "agent-domain-1",
      name: "报价 Agent",
      description: "",
      prompt: "报价",
      level: 4,
      tools: ["CRM 查询", "quote_template", "CRM 查询"],
      category: "regular",
      enabled: false,
      updated_at: "2026-09-10",
    };

    expect(agentPresetToDbPreset(preset)).toEqual({
      apid: "agent-domain-1",
      name: "报价 Agent",
      description: "",
      prompt: "报价",
      intelevel: 4,
      tools: ["crm_query", "quote_template"],
      enabled: false,
      updated_at: "2026-09-10",
      category: "regular",
    });
    expect(agentPresetToConfig(preset)).toMatchObject({ id: "agent-domain-1", apid: "agent-domain-1", enabled: false, level: 4, capabilities: ["crm_query", "quote_template"] });
    expect(displayAgentTools(preset.tools)).toEqual(["CRM 查询", "报价模板"]);
    expect(normalizeAgentTools(["CRM 查询", "crm_query", "  "])).toEqual(["crm_query"]);
    expect(() => normalizeAgentLevel(5)).toThrow();
  });

  it("derives domain agent state from the database DTO without inventing enabled state", () => {
    const preset = dbPresetToAgentPreset({ apid: "agent-db-2", name: "DB Agent", description: "", prompt: "prompt", intelevel: 0, tools: [], enabled: false, updated_at: "2026-09-11", category: "regular" });
    expect(preset).toMatchObject({ id: "agent-db-2", category: "regular", enabled: false, level: 0, updated_at: "2026-09-11" });
    expect(preset).not.toHaveProperty("apid");
    expect(preset).not.toHaveProperty("intelevel");
    expect(agentPresetToDbPreset(preset)).toEqual({ apid: "agent-db-2", name: "DB Agent", description: "", prompt: "prompt", intelevel: 0, tools: [], enabled: false, updated_at: "2026-09-11", category: "regular" });
  });

  it("matches system agent SQL seed levels", () => {
    const systemApids = new Set<string>(Object.values(SYSTEM_AGENT_APIDS));
    const systemPresets = agentPresets.filter((preset) => systemApids.has(String(preset.id)));
    expect(systemPresets).toHaveLength(4);
    expect(systemPresets.every((preset) => preset.level === 0 && preset.tools?.length === 0 && preset.enabled)).toBe(true);
    expect(systemPresets.every((preset) => !("apid" in preset) && !("intelevel" in preset))).toBe(true);
  });

  it("round-trips nullable max tool rounds without converting null to zero", () => {
    const document: DocumentLlmConfig = {
      level: 2,
      base_url: "https://llm.example/v1",
      api_key: "secret",
      model_name: "model",
      system_prompt: "prompt",
      context: 16000,
      max_tool_rounds: null,
    };
    const level = documentToLlmLevelConfig(document);
    expect(level.maxToolRounds).toBeNull();
    expect(llmLevelToDocumentConfig(level)).toMatchObject({ level: 2, max_tool_rounds: null });
    expect(isValidToolRoundLimit(0)).toBe(false);
    expect(isValidToolRoundLimit(null)).toBe(true);
  });
});
