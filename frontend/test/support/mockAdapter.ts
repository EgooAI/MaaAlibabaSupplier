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
import type { OutboxTask, TranslationJobSnapshot } from "@/types/chatOperations";
import { canCancel, canRetry, frameFresh } from "@/features/chat/outbox/outboxModel";
import type { SelfInfo } from "@/types/home";
import type { AliIdList, DataDirCandidates, DataDirStatus, KeyStatus, NetworkStatus, NodeTestResult, SystemStatusSnapshot, TaskSnapshot } from "@/types/status";
import type { OperationsBackend } from "@/services/interfaces";
import { connectionSnapshot } from "@/mock/connectionData";
import { ApiError } from "@/services/httpAdapter";
import type { InboxSettings } from "@/types/inbox";

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
const translationJobs = new Map<string, TranslationJobSnapshot>();
const TRANSLATION_JOB_DELAY_MS = 400;
let connectionStore = structuredClone(connectionSnapshot);
const outboxStore = new Map<string, { scope: string; task: OutboxTask }>();
const outboxScope = () => JSON.stringify([connectionStore.account.data_dir, connectionStore.account.self_ali_id]);
const inboxSettings = new Map<string, InboxSettings>();
function mockInboxSettings() {
  let value = inboxSettings.get(outboxScope());
  if (!value) { value = { timeout_seconds: 86400, inbox_revision: 1 }; inboxSettings.set(outboxScope(), value); }
  return value;
}

function getMockOutbox(id: string, version?: number) {
  const record = outboxStore.get(id);
  if (!record || record.scope !== outboxScope()) throw new Error("任务不存在");
  if (version !== undefined && record.task.version !== version) throw new Error("任务版本已变化");
  return record.task;
}

function changeMockAccount() {
  connectionStore.account.epoch = crypto.randomUUID();
  connectionStore.source = { ...connectionStore.source, epoch: connectionStore.account.epoch, self_ali_id: connectionStore.account.self_ali_id, phase: "idle", ready: false, key_validation: "unverified", auto_enabled: false, freshness: "stale", stale: true, syncing: false, pending: false, last_error: null, error_code: null, retry_at: null };
  connectionStore.capabilities = { read_chat: false, use_ai: false, operate_client: Boolean(connectionStore.account.self_ali_id && connectionStore.client.connected) };
}

function checkMockEpoch(epoch: string) {
  if (epoch !== connectionStore.account.epoch) throw new Error("账号已变化");
}

function requireMockClient() {
  if (!connectionStore.capabilities.operate_client) throw new Error("请先选择卖家账号并接入客户端");
}

export const mockBackend: OperationsBackend = {
  getConnection: () => delay(structuredClone(connectionStore)),
  connectClient: async (epoch) => {
    checkMockEpoch(epoch);
    connectionStore.client = { connected: true, window_generation: crypto.randomUUID(), detail: "客户端窗口已连接" };
    connectionStore.capabilities.operate_client = Boolean(connectionStore.account.self_ali_id);
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
    translationJobs.clear();
    outboxStore.clear();
    inboxSettings.clear();
    return delay(undefined);
  },

  shutdownApp: () => delay(undefined),
  getAppUpdate: () => delay({
    supported: false, reason: "Mock environment does not support application updates", phase: "idle",
    current: { version: "mock", sha: null },
    source: { repository: "", branch: "", workflow: "", artifact: "" },
    candidate: null, downloaded_bytes: 0, total_bytes: null, error: null, last_result: null,
  }),
  checkAppUpdate: async () => { throw new Error("Application updates are unavailable in mocks"); },
  downloadAppUpdate: async () => { throw new Error("Application updates are unavailable in mocks"); },
  installAppUpdate: async () => { throw new Error("Application updates are unavailable in mocks"); },

  requestTranslations: async ({ texts, force = false, conversationId }) => {
    void conversationId;
    requireSystemAgent(SYSTEM_AGENT_APIDS.translation);
    const targets = [...new Set(texts.map((text) => text.trim()).filter(Boolean))];
    if (!targets.length) {
      return delay({ task_id: "", status: "succeeded", message: "没有需要翻译的内容" });
    }
    const task_id = `translation-${crypto.randomUUID()}`;
    const pending: TranslationJobSnapshot = { task_id, status: "pending", message: "等待执行" };
    translationJobs.set(task_id, pending);
    setTimeout(() => {
      if (!translationJobs.has(task_id)) return;
      for (const text of targets) {
        if (!force && translationStore.has(text)) continue;
        const value = mockTranslate(text, force);
        // ABNORMAL（null）不写缓存，保持未翻译态；NO_NEED 以空串哨兵落缓存。
        if (value !== null) translationStore.set(text, value);
      }
      translationJobs.set(task_id, { task_id, status: "succeeded", message: `已翻译 ${targets.length} 条` });
    }, TRANSLATION_JOB_DELAY_MS);
    return delay(pending);
  },

  queryTranslations: async ({ texts }) => {
    const translations: Record<string, string | null> = {};
    for (const raw of texts) {
      const text = (raw ?? "").trim();
      if (!text || text in translations) continue;
      translations[text] = translationStore.get(text) ?? null;
    }
    return delay({ translations });
  },

  getTranslationJob: (taskId) => {
    const job = translationJobs.get(taskId);
    return delay(job ? { ...job } : null, 120);
  },

  listConversations: async (query = {}) => {
    const settings = mockInboxSettings();
    const token = `mock-page-${settings.inbox_revision}`;
    if ((query.offset ?? 0) > 0 && query.pagination_revision !== token) throw new ApiError("分页已过期", "/api/conversations", { status: 409 });
    const q = query.q?.toLowerCase();
    const items = conversationStore.filter((item) => {
      const customer = adaptConversationSummary(item).customer;
      const customerMatch = [customer.name, customer.company, customer.aliId, customer.loginId].some((text) => text?.toLowerCase().includes(q ?? ""));
      const messageMatch = item.messages.some((message) => JSON.stringify(message.message.content).toLowerCase().includes(q ?? ""));
      return (!q || (query.search_scope === "customer" ? customerMatch : query.search_scope === "messages" ? messageMatch : customerMatch || messageMatch))
        && (!query.country || customer.country === query.country) && (!query.tag || customer.tags.includes(query.tag))
        && (!query.reply_state || item.reply_state === query.reply_state)
        && (query.unread === undefined || (item.unread_count > 0) === query.unread)
        && (query.overdue === undefined || item.is_overdue === query.overdue);
    }).sort((a, b) => (b.latest.updated_at ?? "").localeCompare(a.latest.updated_at ?? "") || b.sid - a.sid);
    const offset = query.offset ?? 0;
    const limit = query.limit ?? 50;
    return delay({ items: items.slice(offset, offset + limit).map(adaptConversationSummary), total: items.length, offset, limit, inbox_revision: settings.inbox_revision, pagination_revision: token });
  },

  getInboxSettings: () => delay({ ...mockInboxSettings() }),
  saveInboxSettings: async (timeout_seconds) => {
    if (timeout_seconds < 3600 || timeout_seconds > 604800) throw new Error("超时范围无效");
    const settings = mockInboxSettings();
    settings.timeout_seconds = timeout_seconds;
    ++settings.inbox_revision;
    return delay({ ...settings });
  },
  getInboxOverview: () => delay({
    ...mockInboxSettings(), updated_at: Date.now() / 1000,
    counts: {
      total: conversationStore.length,
      unread: conversationStore.filter((item) => item.unread_count > 0).length,
      needs_reply: conversationStore.filter((item) => item.reply_state === "needs_reply").length,
      overdue: conversationStore.filter((item) => item.is_overdue).length,
      history_pending: conversationStore.filter((item) => item.history_pending).length,
      waiting_customer: conversationStore.filter((item) => item.reply_state === "waiting_customer").length,
    },
  }),
  markConversationRead: async (id, token) => {
    const conversation = conversationStore.find((item) => String(item.sid) === id);
    if (!conversation || token !== conversation.read_snapshot) throw new Error("已读快照无效");
    conversation.unread_count = 0;
    const { unread_count, reply_state, pending_since, due_at, is_overdue, history_pending, uncertain } = conversation;
    return delay({ state: { unread_count, reply_state, pending_since, due_at, is_overdue, history_pending, uncertain, read_seq: 1, snapshot_seq: 1 }, inbox_revision: ++mockInboxSettings().inbox_revision });
  },

  getConversationRevision: () => delay({ ...structuredClone(connectionStore.source), ready: connectionStore.capabilities.read_chat, inbox_revision: mockInboxSettings().inbox_revision, next_due_at: null }),

  getConversation: async (id) => delay(buildConversationDetail(id)),

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

  sendMessage: async ({ conversationId, content, action, idempotency_key }) => {
    requireMockClient();
    const conversation = buildConversationDetail(conversationId);
    if (!idempotency_key || !content.trim()) throw new Error("缺少提交内容或幂等键");
    const existing = [...outboxStore.values()].find((record) => record.scope === outboxScope() && record.task.idempotency_key === idempotency_key)?.task;
    if (existing) {
      if (existing.content !== content || existing.action !== action || String(existing.conversation_id) !== conversationId) throw new Error("幂等键冲突");
      return delay({ outbox: structuredClone(existing) });
    }
    const task: OutboxTask = {
      id: crypto.randomUUID(), conversation_id: Number(conversationId), contact_ali_id: conversation.customer.aliId ?? "",
      login_id: conversation.customer.loginId ?? "", content, action, idempotency_key, status: "queued", version: 1,
      attempt: 1, phase: "search", may_have_sent: false, reason: null, created_at: Date.now() / 1000, updated_at: Date.now() / 1000,
      screenshot_id: null, screenshot_at: null, matched_message_id: null,
    };
    outboxStore.set(task.id, { scope: outboxScope(), task });
    return delay({ outbox: structuredClone(task) });
  },
  listOutbox: (sid) => delay([...outboxStore.values()].filter((record) => record.scope === outboxScope() && String(record.task.conversation_id) === sid).map((record) => structuredClone(record.task))),
  getOutbox: (id) => delay(structuredClone(getMockOutbox(id))),
  confirmOutbox: async (id, version, screenshotId) => {
    requireMockClient();
    const task = getMockOutbox(id, version);
    if (!frameFresh(task) || task.screenshot_id !== screenshotId) throw new Error("截图已变化或过期");
    Object.assign(task, { status: "queued_send", version: version + 1, updated_at: Date.now() / 1000 });
    return delay(structuredClone(task));
  },
  cancelOutbox: async (id, version) => {
    const task = getMockOutbox(id, version);
    if (!canCancel(task)) throw new Error("任务不可取消");
    Object.assign(task, { status: "cancelled", version: version + 1, screenshot_id: null, screenshot_at: null, updated_at: Date.now() / 1000 });
    return delay(structuredClone(task));
  },
  retryOutbox: async (id, version) => {
    requireMockClient();
    const task = getMockOutbox(id, version);
    if (!canRetry(task)) throw new Error("任务不可重试");
    Object.assign(task, { status: "queued", version: version + 1, attempt: task.attempt + 1, screenshot_id: null, screenshot_at: null, updated_at: Date.now() / 1000 });
    return delay(structuredClone(task));
  },
  getOutboxScreenshot: async () => { throw new Error("Mock 不提供真实客户端截图"); },

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
    connectionStore.account.self_ali_id = "";
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

function mockTranslate(text: string, force = false): string | null {
  // 镜像后端三值协议：占位符类文本 → ABNORMAL（null，不缓存）；
  // CJK 为主的文本 → NO_NEED（空串哨兵）；其余返回 mock 译文。
  if (/^\W*\[+.*\]+\W*$/.test(text)) return null;
  const chars = [...text].filter((char) => /\S/.test(char));
  const cjk = chars.filter((char) => /\p{Script=Han}/u.test(char)).length;
  if (cjk / chars.length >= 0.5) return "";
  return force ? `重新翻译 mock 译文：${text}` : `这是 mock 译文：${text}`;
}
