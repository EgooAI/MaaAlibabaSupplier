import { agentConsole, agentPresets, llmLevels, systemAgents } from "@/mock/agentData";
import { businessCards } from "@/mock/cardData";
import { assistantSuggestions, conversationAggregates } from "@/mock/conversationData";
import { mockSelfInfo } from "@/mock/selfData";
import { dataDirStatus, keyStatus, nodeResult, proxyStatus, receiverStatus, systemStatus, taskSnapshots } from "@/mock/statusData";
import { buildConversationExport } from "@/domain/chat/chatModel";
import { nowText } from "@/domain/time";
import { adaptConversationDetail, adaptConversationSummary } from "@/services/chatAdapter";
import { buildSystemStatusSnapshot, taskSnapshotToTaskItem } from "@/domain/status/statusModel";
import { SYSTEM_AGENT_APIDS, agentPresetToConfig, agentPresetToDbPreset, canRunAgentExecution, dbPresetToAgentPreset, documentToLlmLevelConfig, getLatestAgentTestTurn, removeLatestAgentTestTurn, replaceLatestAgentTestReply, upsertLlmLevel } from "@/domain/agent/agentModel";
import type { AgentConsoleState, AgentPreset, AgentTestSession, DbAgentPreset } from "@/types/agent";
import type { BusinessCard } from "@/types/cards";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { ConversationAggregateDto } from "@/types/chatTransport";
import type { SendMessageInput } from "@/types/chatOperations";
import type { SelfInfo } from "@/types/home";
import type { AliIdList, DataDirCandidates, DataDirStatus, KeyStatus, NetworkStatus, NodeTestResult, SystemStatusSnapshot, TaskSnapshot } from "@/types/status";
import type { OperationsBackend } from "@/services/interfaces";
import { connectionSnapshot } from "@/mock/connectionData";

const delay = <T,>(value: T, ms = 280) => new Promise<T>((resolve) => setTimeout(() => resolve(value), ms));

const initialState = {
  selfInfo: mockSelfInfo,
  conversationAggregates,
  cards: businessCards,
  status: systemStatus,
  keyStatus,
  proxyStatus,
  receiverStatus,
  dataDirStatus,
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
let dataDirStatusStore: DataDirStatus = structuredClone(initialState.dataDirStatus);
let taskSnapshotStore: TaskSnapshot[] = structuredClone(initialState.taskSnapshots);
let consoleStore: AgentConsoleState = structuredClone(initialState.console);
let agentPresetStore: AgentPreset[] = structuredClone(initialState.agentPresets);
const translationStore = new Map<string, string>();
let connectionStore = structuredClone(connectionSnapshot);

function changeMockAccount() {
  connectionStore.account.epoch = crypto.randomUUID();
  connectionStore.source = { ...connectionStore.source, epoch: connectionStore.account.epoch, self_ali_id: connectionStore.account.self_ali_id, phase: "idle", ready: false, key_validation: "unverified", auto_enabled: false, freshness: "stale", stale: true, syncing: false, pending: false };
  connectionStore.client.confirmed = false;
  connectionStore.capabilities = { read_chat: false, use_ai: false, operate_client: false };
}

function checkMockEpoch(epoch: string) {
  if (epoch !== connectionStore.account.epoch) throw new Error("账号已变化");
}

function requireMockClient() {
  if (!connectionStore.capabilities.operate_client) throw new Error("请先人工确认客户端卖家身份");
}

export const mockBackend: OperationsBackend = {
  getConnection: () => delay(structuredClone(connectionStore)),
  connectClient: async (epoch) => {
    checkMockEpoch(epoch);
    connectionStore.client = { connected: true, window_generation: crypto.randomUUID(), confirmed: false, detail: "请人工确认当前窗口卖家" };
    connectionStore.capabilities.operate_client = false;
    return delay(structuredClone(connectionStore));
  },
  confirmClient: async (epoch, windowGeneration) => {
    checkMockEpoch(epoch);
    if (!connectionStore.client.connected || windowGeneration !== connectionStore.client.window_generation) throw new Error("客户端窗口已变化");
    connectionStore.client.confirmed = true;
    connectionStore.capabilities.operate_client = true;
    return delay(structuredClone(connectionStore));
  },
  retryConnection: async (epoch) => {
    checkMockEpoch(epoch);
    connectionStore.source = { ...connectionStore.source, phase: "syncing", ready: false, key_validation: "valid", last_checked: Date.now() / 1000, last_attempt: Date.now() / 1000, auto_enabled: true, syncing: true, pending: true, freshness: "syncing", stale: true };
    const selectedEpoch = epoch;
    setTimeout(() => {
      if (connectionStore.account.epoch !== selectedEpoch) return;
      const revision = connectionStore.source.revision + 1;
      connectionStore.source = { ...connectionStore.source, phase: "ready", ready: true, revision, source_revision: revision, applied_source_revision: revision, last_success: Date.now() / 1000, syncing: false, pending: false, freshness: "fresh", stale: false };
      connectionStore.capabilities.read_chat = true;
      connectionStore.capabilities.use_ai = connectionStore.model.configured;
    }, 500);
    return delay(structuredClone(connectionStore));
  },
  getSelfInfo: () => delay(structuredClone(selfInfoStore)),

  resetCache: async () => {
    connectionStore = structuredClone(connectionSnapshot);
    selfInfoStore = structuredClone(initialState.selfInfo);
    conversationStore = structuredClone(initialState.conversationAggregates);
    businessCardStore = structuredClone(initialState.cards);
    keyStatusStore = structuredClone(initialState.keyStatus);
    proxyStatusStore = structuredClone(initialState.proxyStatus);
    receiverStatusStore = structuredClone(initialState.receiverStatus);
    nodeResultStore = structuredClone(initialState.nodeResult);
    dataDirStatusStore = structuredClone(initialState.dataDirStatus);
    taskSnapshotStore = structuredClone(initialState.taskSnapshots);
    agentPresetStore = structuredClone(initialState.agentPresets);
    consoleStore = structuredClone(initialState.console);
    statusStore = buildStatusSnapshot();
    translationStore.clear();
    return delay(undefined);
  },

  shutdownApp: () => delay(undefined),

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

  getConversationRevision: () => delay({ ...structuredClone(connectionStore.source), ready: connectionStore.capabilities.read_chat }),

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
    requireMockClient();
    const execution = executeSendMessage({ conversationId, content, action });
    const conversation = buildConversationDetail(conversationId);
    return delay(structuredClone({ message: undefined, conversation, execution }));
  },

  exportConversations: async ({ conversationIds }) => {
    const selected = conversationIds.map((id) => buildConversationDetail(id));
    return delay(buildConversationExport(selected));
  },

  gotoContact: async () => {
    requireMockClient();
    return delay({ status: "queued" });
  },

  checkUserStatus: () => delay(structuredClone(keyStatusStore)),

  checkMitmProxy: () => delay(structuredClone(proxyStatusStore)),

  checkMitmReceiver: () => delay(structuredClone(receiverStatusStore)),

  runNodeTest: async (entry = "ChatInput_GoToInput") => {
    if (!connectionStore.client.connected) throw new Error("请先接入客户端");
    if (entry !== "ChatInput_GoToInput" && entry !== "ContactSearch_GoToSearch") throw new Error("节点测试入口无效");
    const task = queueMockTask(`节点测试：${entry}`, entry);
    return delay(structuredClone({ success: null, message: task.message, task_snapshot: task }), 360);
  },

  getDataDirStatus: () => delay(structuredClone(dataDirStatusStore)),

  saveDataDirPath: async (path, epoch) => {
    checkMockEpoch(epoch);
    const trimmed = path.trim();
    if (!trimmed) throw new Error("数据目录路径不能为空");
    dataDirStatusStore = { state: "ok", path: trimmed, source: "file", detail: "" };
    connectionStore.data_dir = dataDirStatusStore;
    connectionStore.account.data_dir = trimmed;
    changeMockAccount();
    statusStore = buildStatusSnapshot();
    return delay(structuredClone(dataDirStatusStore));
  },

  listDataDirCandidates: (): Promise<DataDirCandidates> => delay({ candidates: [dataDirStatusStore.path].filter(Boolean) }),

  listAliIds: (): Promise<AliIdList> => delay({ accounts: [], selected: connectionStore.account.self_ali_id }),

  saveAliId: async (aliId, epoch): Promise<AliIdList> => {
    checkMockEpoch(epoch);
    connectionStore.account.self_ali_id = aliId;
    changeMockAccount();
    return delay({ accounts: [], selected: aliId });
  },

  saveAliKey: async (...[, , epoch]): Promise<AliIdList> => {
    checkMockEpoch(epoch);
    changeMockAccount();
    return delay({ accounts: [], selected: connectionStore.account.self_ali_id });
  },

  clearAliKey: async (...[, epoch]): Promise<AliIdList> => {
    checkMockEpoch(epoch);
    changeMockAccount();
    return delay({ accounts: [], selected: connectionStore.account.self_ali_id });
  },

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

  const task = queueMockTask(action === "send" ? "发送聊天消息" : "输入聊天草稿", String(conversationStore[index].sid));
  return { success: null, message: task.message, task_snapshot: task };
}

function queueMockTask(description: string, target: string): TaskSnapshot {
  const timestamp = Math.floor(Date.now() / 1000);
  const task: TaskSnapshot = {
    task_id: `task-${crypto.randomUUID()}`,
    description,
    status: "pending",
    message: "任务已提交，等待执行",
    result: null,
    target,
    created_at: timestamp,
    started_at: null,
    completed_at: null,
  };

  taskSnapshotStore = [task, ...taskSnapshotStore];
  statusStore = buildStatusSnapshot();
  return task;
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
    dataDirStatus: dataDirStatusStore,
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
