import { agentConsole, agentPresets, llmLevels, systemAgents } from "@/mock/agentData";
import { businessCards } from "@/mock/cardData";
import { assistantSuggestions, conversationAggregates } from "@/mock/conversationData";
import { mockSelfInfo } from "@/mock/selfData";
import { keyStatus, nodeResult, proxyStatus, receiverStatus, systemStatus, taskSnapshots } from "@/mock/statusData";
import { buildConversationExport } from "@/domain/chat/chatModel";
import { adaptConversationDetail, adaptConversationSummary } from "@/services/chatAdapter";
import { buildSystemStatusSnapshot, taskSnapshotToTaskItem } from "@/domain/status/statusModel";
import { SYSTEM_AGENT_APIDS, agentPresetToConfig, agentPresetToDbPreset, canRunAgentExecution, dbPresetToAgentPreset, documentToLlmLevelConfig, getLatestAgentTestTurn, removeLatestAgentTestTurn, replaceLatestAgentTestReply, upsertLlmLevel } from "@/domain/agent/agentModel";
import type { AgentConsoleState, AgentPreset, AgentTestSession, DbAgentPreset } from "@/types/agent";
import type { BusinessCard } from "@/types/cards";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { ConversationAggregateDto, Message } from "@/types/chatTransport";
import type { SendMessageInput } from "@/types/chatOperations";
import type { SelfInfo } from "@/types/home";
import type { KeyStatus, NetworkStatus, NodeTestResult, SystemStatusSnapshot, TaskSnapshot } from "@/types/status";
import type { OperationsBackend } from "./interfaces";

const delay = <T,>(value: T, ms = 280) => new Promise<T>((resolve) => setTimeout(() => resolve(value), ms));

const initialState = {
  selfInfo: mockSelfInfo,
  conversationAggregates,
  cards: businessCards,
  status: systemStatus,
  keyStatus,
  proxyStatus,
  receiverStatus,
  nodeResult,
  taskSnapshots,
  console: agentConsole,
  agentPresets,
};

let selfInfoStore: SelfInfo | null = structuredClone(initialState.selfInfo);
let conversationStore: ConversationAggregateDto[] = structuredClone(initialState.conversationAggregates);
let businessCardStore: BusinessCard[] = structuredClone(initialState.cards);
let statusStore: SystemStatusSnapshot = structuredClone(initialState.status);
let keyStatusStore: KeyStatus = structuredClone(initialState.keyStatus);
let proxyStatusStore: NetworkStatus = structuredClone(initialState.proxyStatus);
let receiverStatusStore: NetworkStatus = structuredClone(initialState.receiverStatus);
let nodeResultStore: NodeTestResult = structuredClone(initialState.nodeResult);
let taskSnapshotStore: TaskSnapshot[] = structuredClone(initialState.taskSnapshots);
let consoleStore: AgentConsoleState = structuredClone(initialState.console);
let agentPresetStore: AgentPreset[] = structuredClone(initialState.agentPresets);
const translationStore = new Map<string, string>();

export const mockBackend: OperationsBackend = {
  getSelfInfo: () => delay(structuredClone(selfInfoStore)),

  resetCache: async () => {
    selfInfoStore = structuredClone(initialState.selfInfo);
    conversationStore = structuredClone(initialState.conversationAggregates);
    businessCardStore = structuredClone(initialState.cards);
    keyStatusStore = structuredClone(initialState.keyStatus);
    proxyStatusStore = structuredClone(initialState.proxyStatus);
    receiverStatusStore = structuredClone(initialState.receiverStatus);
    nodeResultStore = structuredClone(initialState.nodeResult);
    taskSnapshotStore = structuredClone(initialState.taskSnapshots);
    agentPresetStore = structuredClone(initialState.agentPresets);
    consoleStore = structuredClone(initialState.console);
    statusStore = buildStatusSnapshot();
    translationStore.clear();
    return delay(undefined);
  },

  requestTranslations: async ({ texts, force = false }) => {
    requireSystemAgent(SYSTEM_AGENT_APIDS.translation);
    const targets = texts.map((text) => text.trim()).filter(Boolean);
    let savedCount = 0;

    for (const text of targets) {
      if (force || !translationStore.has(text)) {
        translationStore.set(text, mockTranslate(text));
        savedCount += 1;
      }
    }

    return delay({ saved_count: savedCount, translated_text: targets[0] ? translationStore.get(targets[0]) ?? null : null, cached: Boolean(targets[0] && savedCount === 0) });
  },

  getTranslation: (text) => delay(translationStore.get(text) ?? null),

  listConversations: () => delay(conversationStore.map((conversation) => adaptConversationSummary(conversation))),

  getConversation: async (id) => delay(buildConversationDetail(id)),

  translateMessage: async ({ conversationId, messageId, targetLanguage }) => {
    requireSystemAgent(SYSTEM_AGENT_APIDS.translation);
    const detail = buildConversationDetail(conversationId);
    const message = detail.messages.find((item) => item.id === messageId);
    if (!message) throw new Error("消息不存在");
    const translatedContent = targetLanguage === "zh-CN" ? mockTranslate(message.content) : `Mock translation: ${message.content}`;
    translationStore.set(`${conversationId}:${messageId}:${targetLanguage}`, translatedContent);
    return delay({ messageId, translatedContent });
  },

  regenerateTranslation: async ({ conversationId, messageId, targetLanguage }) => {
    requireSystemAgent(SYSTEM_AGENT_APIDS.translation);
    const detail = buildConversationDetail(conversationId);
    const message = detail.messages.find((item) => item.id === messageId);
    if (!message) throw new Error("消息不存在");
    const translatedContent = targetLanguage === "zh-CN" ? `重新翻译：${mockTranslate(message.content)}` : `Regenerated mock translation: ${message.content}`;
    translationStore.set(`${conversationId}:${messageId}:${targetLanguage}`, translatedContent);
    return delay({ messageId, translatedContent });
  },

  getAssistantSuggestions: async (conversationId) => {
    requireSystemAgent(SYSTEM_AGENT_APIDS.replySuggestion);
    buildConversationDetail(conversationId);
    return delay(structuredClone(assistantSuggestions));
  },

  analyzeConversation: async (conversationId) => {
    requireSystemAgent(SYSTEM_AGENT_APIDS.intentAnalysis);
    const detail = buildConversationDetail(conversationId);
    if (!detail.analysis) throw new Error("会话暂无分析结果");
    return delay(detail.analysis);
  },

  sendMessage: async ({ conversationId, content, action = "send" }: SendMessageInput) => {
    const execution = executeSendMessage({ conversationId, content, action });
    const conversation = buildConversationDetail(conversationId);
    const message = action === "send" ? conversation.messages.at(-1) : undefined;
    if (action === "send" && !message) throw new Error("发送消息后未找到消息");
    return delay(structuredClone({ message, conversation, execution }));
  },

  exportConversations: async ({ conversationIds }) => {
    const selected = conversationIds.map((id) => buildConversationDetail(id));
    return delay(buildConversationExport(selected));
  },

  gotoContact: async () => delay({ status: "queued" }),

  checkUserStatus: () => delay(structuredClone(keyStatusStore)),

  checkMitmProxy: () => delay(structuredClone(proxyStatusStore)),

  checkMitmReceiver: () => delay(structuredClone(receiverStatusStore)),

  runNodeTest: () => delay(structuredClone(nodeResultStore), 360),

  listTaskSnapshots: () => delay(structuredClone(taskSnapshotStore)),

  getSystemStatus: () => delay(structuredClone(statusStore)),

  refreshSystemStatus: async () => {
    proxyStatusStore = { ...proxyStatusStore, latency_ms: Math.max(48, (proxyStatusStore.latency_ms ?? 180) - 12) };
    receiverStatusStore = { ...receiverStatusStore, latency_ms: Math.max(48, (receiverStatusStore.latency_ms ?? 90) + 7) };
    statusStore = buildStatusSnapshot();
    return delay(structuredClone(statusStore), 420);
  },

  createTestTask: async ({ type, target }) => {
    const timestamp = Math.floor(Date.now() / 1000);
    const snapshot: TaskSnapshot = {
      task_id: `task-${crypto.randomUUID()}`,
      description: type,
      status: "pending",
      message: "前端测试任务已提交",
      result: null,
      target,
      created_at: timestamp,
      started_at: null,
      completed_at: null,
    };
    taskSnapshotStore = [snapshot, ...taskSnapshotStore];
    statusStore = buildStatusSnapshot();
    return delay(taskSnapshotToTaskItem(snapshot));
  },

  getAgentConsole: () => delay(structuredClone(consoleStore)),

  saveLlmConfig: async (input) => {
    const savedDocument = structuredClone(input);
    const savedLevel = documentToLlmLevelConfig(savedDocument);
    const nextLevels = upsertLlmLevel(consoleStore.llmLevels ?? llmLevels, savedLevel);
    consoleStore = { ...consoleStore, llmLevels: nextLevels };
    return delay(savedDocument);
  },

  saveAgentPreset: async (input: DbAgentPreset) => {
    const current = agentPresetStore.find((preset) => preset.id === input.apid);
    const normalized = dbPresetToAgentPreset(input, current, input.updated_at);
    agentPresetStore = current ? agentPresetStore.map((preset) => (preset.id === input.apid ? normalized : preset)) : [normalized, ...agentPresetStore];
    syncConsoleAgents();
    return delay(agentPresetToDbPreset(normalized));
  },

  deleteAgentPreset: async (id) => {
    const target = agentPresetStore.find((preset) => preset.id === id);
    if (!target || target.category === "system") return delay(false);
    agentPresetStore = agentPresetStore.filter((preset) => preset.id !== id);
    consoleStore = { ...consoleStore, history: consoleStore.history.filter((session) => session.agentId !== id) };
    syncConsoleAgents();
    return delay(true);
  },

  restoreSystemAgentDefault: async (apid) => {
    const source = initialState.agentPresets.find((preset) => preset.id === apid);
    if (!source) throw new Error("系统 Agent 默认配置不存在");
    agentPresetStore = agentPresetStore.map((preset) => preset.id === apid ? structuredClone(source) : preset);
    syncConsoleAgents();
    return delay(agentPresetToDbPreset(source));
  },

  listSystemAgentDefinitions: () => delay(structuredClone(systemAgents)),

  runAgentTest: async ({ agentId, content, sessionId }) => {
    const agent = consoleStore.agents.find((item) => item.id === agentId);
    if (!canRunAgentExecution(agent)) throw new Error("Agent 不存在或未启用");
    const now = nowText();

    if (sessionId) {
      const existingSession = getAgentSession(sessionId);
      if (existingSession.agentId !== agentId || !content?.trim()) throw new Error("Agent 会话请求无效");
      const userMessage = { id: `user-${crypto.randomUUID()}`, role: "user" as const, content: content.trim(), createdAt: now };
      const reply = {
        id: `assistant-${crypto.randomUUID()}`,
        role: "assistant" as const,
        content: `模拟回复：${agent.name} 已根据接口文档约定返回处理建议。`,
        createdAt: now,
      };
      const session = { ...existingSession, messages: [...existingSession.messages, userMessage, reply] };
      consoleStore = { ...consoleStore, history: consoleStore.history.map((item) => item.id === session.id ? session : item) };
      return delay({ session, reply }, 520);
    }

    const session: AgentTestSession = {
      id: `session-${crypto.randomUUID()}`,
      title: `${agent.name} 测试`,
      agentId,
      createdAt: now,
      messages: content?.trim() ? [
        { id: `user-${crypto.randomUUID()}`, role: "user", content: content.trim(), createdAt: now },
        {
          id: `assistant-${crypto.randomUUID()}`,
          role: "assistant",
          content: `模拟回复：${agent.name} 已根据接口文档约定返回处理建议。`,
          createdAt: now,
        },
      ] : [],
    };
    consoleStore = { ...consoleStore, history: [session, ...consoleStore.history] };
    return delay({ session, reply: session.messages[1] }, 520);
  },

  listAgentTestHistory: () => delay(structuredClone(consoleStore.history)),

  undoAgentTestSession: async (id) => {
    const source = getAgentSession(id);
    const session = removeLatestAgentTestTurn(source);
    if (!session) throw new Error("会话没有可撤销的完整问答轮次");
    consoleStore = { ...consoleStore, history: consoleStore.history.map((item) => item.id === id ? session : item) };
    return delay(structuredClone(session), 420);
  },

  regenerateAgentTestSessionReply: async (id) => {
    const source = getAgentSession(id);
    const agent = consoleStore.agents.find((item) => item.id === source.agentId);
    if (!canRunAgentExecution(agent)) throw new Error("Agent 不存在或未启用");
    const turn = getLatestAgentTestTurn(source);
    if (!turn) throw new Error("会话没有可重新回复的完整问答轮次");
    const reply = {
      ...turn.assistant,
      id: `assistant-${crypto.randomUUID()}`,
      content: `模拟重新回复：已根据“${turn.user.content}”生成新的处理建议。`,
      createdAt: nowText(),
    };
    const session = replaceLatestAgentTestReply(source, reply);
    if (!session) throw new Error("会话没有可重新回复的完整问答轮次");
    consoleStore = { ...consoleStore, history: consoleStore.history.map((item) => item.id === id ? session : item) };
    return delay(structuredClone(session), 520);
  },

  deleteAgentTestSession: async (id) => {
    const source = getAgentSession(id);
    consoleStore = { ...consoleStore, history: consoleStore.history.filter((session) => session.id !== source.id) };
    return delay(undefined);
  },

  branchAgentTestSession: async (id) => {
    const source = getAgentSession(id);
    const branch = { ...structuredClone(source), id: `session-${crypto.randomUUID()}`, title: `${source.title} - 分支`, createdAt: nowText() };
    consoleStore = { ...consoleStore, history: [branch, ...consoleStore.history] };
    return delay(branch);
  },
};

function buildConversationDetail(id: string): ConversationDetail {
  const conversation = conversationStore.find((item) => conversationMatchesId(item, id));
  if (!conversation) throw new Error("会话不存在");
  return adaptConversationDetail(conversation, { cards: businessCardStore });
}

function executeSendMessage(input: SendMessageInput) {
  const action = input.action ?? "send";
  if (action !== "send" && action !== "test") throw new Error("消息动作无效");
  const index = conversationStore.findIndex((item) => conversationMatchesId(item, input.conversationId));
  if (index < 0) throw new Error("会话不存在");
  const content = input.content.trim();
  if (!content) throw new Error("消息内容不能为空");

  const target = conversationStore[index];
  const timestamp = Math.floor(Date.now() / 1000);
  const task: TaskSnapshot = {
    task_id: `task-${crypto.randomUUID()}`,
    description: action === "send" ? "发送聊天消息" : "输入聊天草稿",
    status: "succeeded",
    message: action === "send" ? "消息已发送" : "消息已输入但未发送",
    result: [true, action === "send" ? "发送完成" : "输入完成"],
    target: String(target.sid),
    created_at: timestamp,
    started_at: timestamp,
    completed_at: timestamp + 1,
  };

  if (action === "send") {
    const externalMid = `msg-${crypto.randomUUID()}`;
    const nextUpdatedAt = nowText();
    const message: Message = {
      external_mid: externalMid,
      sid: target.sid,
      sender: selfInfoStore?.aid ?? 0,
      read: true,
      content,
      type: "text",
    };
    conversationStore[index] = {
      ...target,
      messages: [...target.messages, { message, created_at: nextUpdatedAt, role: "seller" }],
      latest: { updated_at: nextUpdatedAt, content },
      unread_count: 0,
      status: "following",
    };
  }

  taskSnapshotStore = [task, ...taskSnapshotStore];
  statusStore = buildStatusSnapshot();
  return { success: true, message: task.message, task_snapshot: task };
}

function conversationMatchesId(conversation: ConversationAggregateDto, id: string) {
  return String(conversation.sid) === id;
}

function getAgentSession(id: string) {
  const session = consoleStore.history.find((item) => item.id === id);
  if (!session) throw new Error("Agent 会话不存在");
  return session;
}

function requireSystemAgent(apid: string) {
  const agent = consoleStore.agents.find((item) => item.id === apid && item.category === "system");
  if (!canRunAgentExecution(agent)) throw new Error("系统 Agent 不存在或未启用");
}

function buildStatusSnapshot() {
  return buildSystemStatusSnapshot({
    userStatus: keyStatusStore,
    proxyStatus: proxyStatusStore,
    receiverStatus: receiverStatusStore,
    nodeResult: nodeResultStore,
    taskSnapshots: taskSnapshotStore,
    updatedAt: nowText(),
  });
}

function syncConsoleAgents() {
  consoleStore = {
    ...consoleStore,
    agents: agentPresetStore.map(agentPresetToConfig),
    agentPresets: agentPresetStore,
  };
}

function mockTranslate(text: string) {
  if (!text) return "";
  return `这是 mock 译文：${text}`;
}

function nowText() {
  return new Date().toLocaleString("zh-CN", { hour12: false }).replaceAll("/", "-");
}
