import { describe, expect, it } from "vitest";
import { agentPresetToConfig, agentPresetToDbPreset, canRegenerateAgentTestReply, canUndoAgentTestTurn, copyLlmLevelConfig, createRegularAgentPreset, dbPresetToAgentPreset, documentToLlmLevelConfig, filterAgentTestSessionsByCategory, isSystemAgentModified, isValidToolRoundLimit, llmLevelToDocumentConfig, nextAgentTestSessionId, normalizeAgentEditValues, normalizeAgentLevel, normalizeAgentTools, removeLatestAgentTestTurn, replaceLatestAgentTestReply, unregisteredAgentTools } from "@/domain/agent/agentModel";
import type { AgentConfig, AgentPreset, AgentTestSession, AgentToolDefinition, DocumentLlmConfig, LlmLevelConfig, SystemAgentDefinition } from "@/types/agent";

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
  it("filters test sessions by the agents in a category", () => {
    expect(filterAgentTestSessionsByCategory(sessions, agents, "system").map((session) => session.id)).toEqual(["session-system"]);
    expect(filterAgentTestSessionsByCategory(sessions, agents, "regular").map((session) => session.id)).toEqual(["session-regular"]);
    expect(filterAgentTestSessionsByCategory(sessions, [], "system")).toEqual([]);
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

  it("normalizes tool names and ignores object prototype keys", () => {
    expect(normalizeAgentTools(["toString", " calculate ", "calculate", "  "])).toEqual(["toString", "calculate"]);
  });

  it("normalizes edit values before building presets", () => {
    expect(() => normalizeAgentEditValues({ name: " 报价 ", description: undefined, prompt: " prompt ", level: 2.5, capabilities: [] })).toThrow("Agent 等级必须是 0 到 4 的整数");
    expect(() => normalizeAgentEditValues({ name: " ", prompt: "prompt", level: 0 })).toThrow("请输入名称");
    expect(createRegularAgentPreset({ name: " 报价 ", prompt: " prompt ", level: 2, capabilities: [" calculate "] }, "2026-09-11", "agent-new")).toMatchObject({ name: "报价", description: "", prompt: "prompt", level: 2, tools: ["calculate"] });
  });

  it("maps the domain preset to the database DTO at the service boundary", () => {
    const preset: AgentPreset = {
      id: "agent-domain-1",
      name: "报价 Agent",
      description: "",
      prompt: "报价",
      level: 4,
      tools: ["calculate", " calculate ", "calculate"],
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
      tools: ["calculate"],
      enabled: false,
      updated_at: "2026-09-10",
      category: "regular",
    });
    expect(agentPresetToConfig(preset)).toMatchObject({ id: "agent-domain-1", apid: "agent-domain-1", enabled: false, level: 4, capabilities: ["calculate"] });
    expect(() => normalizeAgentLevel(5)).toThrow();
  });

  it("reports attached tools that the backend never registered", () => {
    const available: AgentToolDefinition[] = [{ name: "calculate", description: "arithmetic" }];
    expect(unregisteredAgentTools(["calculate"], available)).toEqual([]);
    expect(unregisteredAgentTools(["crm_query", "quote_template", "crm_query"], available)).toEqual(["crm_query", "quote_template"]);
    expect(unregisteredAgentTools(["crm_query"], [])).toEqual(["crm_query"]);
  });

  it("derives domain agent state from the database DTO without inventing enabled state", () => {
    const preset = dbPresetToAgentPreset({ apid: "agent-db-2", name: "DB Agent", description: "", prompt: "prompt", intelevel: 0, tools: [], enabled: false, updated_at: "2026-09-11", category: "regular" });
    expect(preset).toMatchObject({ id: "agent-db-2", category: "regular", enabled: false, level: 0, updated_at: "2026-09-11" });
    expect(preset).not.toHaveProperty("apid");
    expect(preset).not.toHaveProperty("intelevel");
    expect(agentPresetToDbPreset(preset)).toEqual({ apid: "agent-db-2", name: "DB Agent", description: "", prompt: "prompt", intelevel: 0, tools: [], enabled: false, updated_at: "2026-09-11", category: "regular" });
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
    expect(level.max_tool_rounds).toBeNull();
    expect(llmLevelToDocumentConfig(level)).toMatchObject({ level: 2, max_tool_rounds: null });
    expect(isValidToolRoundLimit(0)).toBe(false);
    expect(isValidToolRoundLimit(null)).toBe(true);
  });

  it("copies a level's whole configuration onto the selected target level", () => {
    const source: LlmLevelConfig = {
      level: 0,
      base_url: "https://llm.example/v1",
      api_key: "secret",
      model_name: "model-a",
      system_prompt: "prompt",
      context: 16000,
      max_tool_rounds: null,
    };
    expect(copyLlmLevelConfig(source, 3)).toEqual({ ...source, level: 3 });
    expect(source.level).toBe(0);
  });

  it("detects modified system agents against their built-in default", () => {
    const definition: SystemAgentDefinition = {
      display_name: "翻译",
      apid: "agent-system-1",
      description: "d",
      prompt: "默认提示词",
      intelevel: 0,
      tools: [],
    };
    const base: AgentConfig = {
      id: "agent-system-1", name: "翻译", category: "system", enabled: true,
      capabilities: [], description: "d", updatedAt: "", prompt: "默认提示词", level: 0, apid: "agent-system-1",
    };
    expect(isSystemAgentModified(base, definition)).toBe(false);
    expect(isSystemAgentModified({ ...base, prompt: "改过的提示词" }, definition)).toBe(true);
    expect(isSystemAgentModified({ ...base, level: 2 }, definition)).toBe(true);
    expect(isSystemAgentModified(base, undefined)).toBe(false);
  });

  it("carries the revision note into the database DTO", () => {
    const preset: AgentPreset = {
      id: "agent-domain-2", name: "报价 Agent", description: "", prompt: "报价",
      level: 0, tools: [], category: "regular", enabled: true, updated_at: "2026-09-10",
    };
    const dto = agentPresetToDbPreset(preset, { revisionNote: "载入历史版本 2026-09-10" });
    expect(dto.revision_note).toBe("载入历史版本 2026-09-10");
    expect(agentPresetToDbPreset(preset).revision_note).toBeUndefined();
  });
});
