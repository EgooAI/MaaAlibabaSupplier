import type { DbAgentPreset, DocumentLlmConfig } from "@/types/agent";
import { adaptConversationDetail, adaptConversationSummary } from "@/services/chatAdapter";
import type { ConversationAggregateDto } from "@/types/chatTransport";
import type { ConversationRevision, TranslationJobSnapshot } from "@/types/chatOperations";
import type { ConversationPage } from "@/types/inbox";
import type { ApiResponse } from "@/types/common";
import type { OperationsBackend } from "./interfaces";
import { accountSession, AccountChangedError, captureAccount } from "./accountSession";
import { authSession, captureAuth } from "./authSession";

const REQUEST_TIMEOUT_MS = 8000;

export class ApiError extends Error {
  readonly code?: number;
  readonly status?: number;
  readonly path: string;

  constructor(message: string, path: string, options?: { code?: number; status?: number; cause?: unknown }) {
    super(message, options?.cause !== undefined ? { cause: options.cause } : undefined);
    this.name = "ApiError";
    this.path = path;
    this.code = options?.code;
    this.status = options?.status;
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function parseApiResponse<T>(payload: unknown, path: string): T {
  const envelope = payload as Partial<ApiResponse<T>>;
  if (!isPlainObject(payload) || typeof envelope.code !== "number" || typeof envelope.msg !== "string") {
    throw new ApiError(`API protocol error: ${path}`, path);
  }
  if (envelope.code !== 0) throw new ApiError(envelope.msg, path, { code: envelope.code });
  return envelope.data as T;
}

function parseJsonPayload(text: string, path: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch (error) {
    throw new ApiError(`API response body is not valid JSON: ${path}`, path, { cause: error });
  }
}

async function request<T>(path: string, mode: "json" | "void" | "png", init?: RequestInit): Promise<T> {
  const auth = captureAuth();
  const account = prepareAccountRequest(path, init);
  const headers = new Headers(account.init?.headers);
  headers.set("Authorization", `Bearer ${auth.token}`);
  const signal = AbortSignal.any([auth.signal, AbortSignal.timeout(REQUEST_TIMEOUT_MS), ...(init?.signal ? [init.signal] : [])]);
  try {
    const response = await fetch(path, requestInit({ ...account.init, headers, signal, cache: "no-store", credentials: "omit" }));
    auth.assertCurrent();
    // Authentication failures need no account epoch (including screenshot failures).
    if (response.status === 401) {
      void authSession.unauthorized(auth.generation);
      auth.assertCurrent();
    }
    const body = mode === "png" && response.ok ? await response.blob() : await response.text();
    auth.assertCurrent();
    account.ticket?.assertCurrent(response.headers.get("X-Account-Epoch"));
    if (!response.ok) throw new ApiError(envelopeMessage(body as string, path), path, { status: response.status });
    if (mode === "png") {
      if (response.headers.get("X-Account-Epoch") !== account.ticket!.epoch) throw new AccountChangedError();
      if (response.headers.get("Content-Type")?.split(";")[0] !== "image/png") throw new ApiError("截图格式无效", path);
      return body as T;
    }
    const text = body as string;
    if (!text.trim()) {
      if (mode === "void") return undefined as T;
      throw new ApiError(`API response body is empty: ${path}`, path);
    }
    const data = parseApiResponse<T>(parseJsonPayload(text, path), path);
    return mode === "void" ? undefined as T : data;
  } catch (error) {
    auth.assertCurrent();
    account.ticket?.assertCurrent();
    if (error instanceof ApiError || error instanceof AccountChangedError) throw error;
    throw new ApiError(`API request failed: ${path}`, path, { cause: error });
  }
}

const requestJson = <T,>(path: string, init?: RequestInit) => request<T>(path, "json", init);
const requestVoid = (path: string, init?: RequestInit) => request<void>(path, "void", init);

function envelopeMessage(text: string, path: string): string {
  try {
    const payload = JSON.parse(text) as Partial<ApiResponse<unknown>>;
    if (isPlainObject(payload) && typeof payload.msg === "string" && payload.msg) return payload.msg;
  } catch {
    // fall through
  }
  return `API request failed: ${path}`;
}

export function requestInit(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return { ...init, headers };
}

function prepareAccountRequest(path: string, init?: RequestInit) {
  path = new URL(path, "http://localhost").pathname;
  const settingsWrite = /^\/api\/settings\/(alibaba-data-dir|ali-id|ali-keys)(?:\/|$)/.test(path) && (init?.method === "PUT" || init?.method === "DELETE");
  if (settingsWrite) {
    // Settings writes intentionally run after local invalidation and may change the epoch.
    if (!new Headers(init?.headers).get("X-Account-Epoch")) throw new AccountChangedError();
    return { init };
  }
  const scoped = path === "/api/settings/connection/retry" || /^\/api\/(inbox(?:\/|$)|outbox(?:\/|$)|conversations(?:\/|$)|messages(?:\/|$)|self-info(?:\/|$)|cache\/reset(?:\/|$)|status\/node-test(?:\/|$))/.test(path);
  if (!scoped) return { init };
  const ticket = captureAccount();
  const { capabilities, client } = accountSession.get().snapshot!;
  const operates = /\/goto-contact$/.test(path) || (/\/conversations\/[^/]+\/messages$/.test(path) && init?.method === "POST") || /\/outbox\/[^/]+\/(confirm|retry)$/.test(path);
  const usesAi = /\/(suggestions|analysis)$/.test(path) || (path === "/api/messages/translations" && init?.method === "POST");
  if (operates && !capabilities.operate_client) throw new Error("请先在设置中选择卖家账号并接入客户端");
  if (path === "/api/status/node-test" && !client.connected) throw new Error("请先在设置中接入客户端，再运行只读界面检查");
  if (usesAi && !capabilities.use_ai) throw new Error("AI 暂不可用，请检查账号数据和模型配置");
  const headers = new Headers(init?.headers);
  headers.set("X-Account-Epoch", ticket.epoch);
  return { ticket, init: { ...init, headers } };
}

export const httpBackend: OperationsBackend = {
  getConnection: () => requestJson("/api/settings/connection"),
  connectClient: (epoch) => requestJson("/api/settings/connection/connect", { method: "POST", body: JSON.stringify({ epoch }) }),
  retryConnection: async (epoch) => {
    captureAccount().assertCurrent(epoch);
    return requestJson("/api/settings/connection/retry", { method: "POST", body: JSON.stringify({ epoch }) });
  },
  getSelfInfo: () => requestJson("/api/self-info"),
  resetCache: () => requestVoid("/api/cache/reset", { method: "POST" }),
  shutdownApp: () => requestVoid("/api/app/shutdown", { method: "POST" }),
  getAppUpdate: () => requestJson("/api/app/update"),
  checkAppUpdate: () => requestJson("/api/app/update/check", { method: "POST", body: JSON.stringify({}) }),
  downloadAppUpdate: (candidateId) => requestJson("/api/app/update/download", { method: "POST", body: JSON.stringify({ candidate_id: candidateId }) }),
  installAppUpdate: (candidateId) => requestJson("/api/app/update/install", { method: "POST", body: JSON.stringify({ candidate_id: candidateId, confirm: true }) }),

  requestTranslations: (input) => requestJson("/api/messages/translations", { method: "POST", body: JSON.stringify(input) }),
  queryTranslations: (input) => requestJson("/api/messages/translations/query", { method: "POST", body: JSON.stringify(input) }),
  getTranslationJob: async (taskId) => {
    try {
      return await requestJson<TranslationJobSnapshot>(`/api/messages/translations/jobs/${encodeURIComponent(taskId)}`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  },

  listConversations: async (query = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) params.set(key, String(value));
    }
    const page = await requestJson<ConversationPage<ConversationAggregateDto>>(`/api/conversations?${params}`, { cache: "no-store" });
    return { ...page, items: page.items.map(adaptConversationSummary) };
  },
  markConversationRead: (id, readSnapshot) => requestJson(`/api/conversations/${encodeURIComponent(id)}/read`, { method: "POST", body: JSON.stringify({ read_snapshot: readSnapshot }) }),
  getInboxOverview: () => requestJson("/api/inbox/overview", { cache: "no-store" }),
  getInboxSettings: () => requestJson("/api/inbox/settings", { cache: "no-store" }),
  saveInboxSettings: (timeoutSeconds) => requestJson("/api/inbox/settings", { method: "PUT", body: JSON.stringify({ timeout_seconds: timeoutSeconds }) }),
  getConversationRevision: () => requestJson<ConversationRevision>("/api/conversations/revision"),
  getConversation: async (id) => {
    const aggregate = await requestJson<ConversationAggregateDto>(`/api/conversations/${encodeURIComponent(id)}`);
    return adaptConversationDetail(aggregate);
  },
  getAssistantSuggestions: (conversationId) => requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/suggestions`),
  analyzeConversation: (conversationId) => requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/analysis`),
  sendMessage: async ({ conversationId, ...input }) => {
    if (!input.idempotency_key?.trim()) throw new Error("提交消息必须提供已持久化的幂等键");
    return requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, { method: "POST", body: JSON.stringify(input) });
  },
  listOutbox: (id) => requestJson(`/api/conversations/${encodeURIComponent(id)}/outbox`, { cache: "no-store" }),
  getOutbox: (id) => requestJson(`/api/outbox/${encodeURIComponent(id)}`, { cache: "no-store" }),
  confirmOutbox: (id, version, screenshotId) => requestJson(`/api/outbox/${encodeURIComponent(id)}/confirm`, { method: "POST", body: JSON.stringify({ version, screenshot_id: screenshotId }) }),
  cancelOutbox: (id, version) => requestJson(`/api/outbox/${encodeURIComponent(id)}/cancel`, { method: "POST", body: JSON.stringify({ version }) }),
  retryOutbox: (id, version) => requestJson(`/api/outbox/${encodeURIComponent(id)}/retry`, { method: "POST", body: JSON.stringify({ version }) }),
  getOutboxScreenshot: async (id, screenshotId, version) => {
    const path = `/api/outbox/${encodeURIComponent(id)}/screenshot/${encodeURIComponent(screenshotId)}?version=${encodeURIComponent(version)}`;
    return request<Blob>(path, "png", { cache: "no-store" });
  },
  exportConversations: (input) => requestJson("/api/conversations/export", { method: "POST", body: JSON.stringify(input) }),
  gotoContact: (conversationId, loginId) => requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/goto-contact`, { method: "POST", body: JSON.stringify({ login_id: loginId }) }),

  checkUserStatus: () => requestJson("/api/status/user"),
  checkMitmProxy: () => requestJson("/api/status/mitm-proxy"),
  checkMitmReceiver: () => requestJson("/api/status/mitm-receiver"),
  runNodeTest: (entry) => requestJson("/api/status/node-test", { method: "POST", body: JSON.stringify({ entry: entry ?? "ChatInput_GoToInput" }) }),
  listTaskSnapshots: () => requestJson("/api/status/tasks"),
  getSystemStatus: () => requestJson("/api/status"),
  createTestTask: (input) => requestJson("/api/status/test-tasks", { method: "POST", body: JSON.stringify(input) }),

  getDataDirStatus: () => requestJson("/api/settings/alibaba-data-dir"),
  saveDataDirPath: (path, epoch) => requestJson("/api/settings/alibaba-data-dir", { method: "PUT", headers: { "X-Account-Epoch": epoch }, body: JSON.stringify({ path }) }),
  listDataDirCandidates: () => requestJson("/api/settings/alibaba-data-dir/candidates"),

  listAliIds: () => requestJson("/api/settings/ali-ids"),
  saveAliId: (aliId, epoch) => requestJson("/api/settings/ali-id", { method: "PUT", headers: { "X-Account-Epoch": epoch }, body: JSON.stringify({ ali_id: aliId }) }),
  saveAliKey: (aliId, aesKeyHex, epoch) => requestJson("/api/settings/ali-keys", { method: "PUT", headers: { "X-Account-Epoch": epoch }, body: JSON.stringify({ ali_id: aliId, aes_key_hex: aesKeyHex }) }),
  clearAliKey: (aliId, epoch) => requestJson(`/api/settings/ali-keys/${encodeURIComponent(aliId)}`, { method: "DELETE", headers: { "X-Account-Epoch": epoch } }),

  getAgentConsole: () => requestJson("/api/agent/console"),
  saveLlmConfig: (input: DocumentLlmConfig) => requestJson("/api/agent/llm-config", { method: "PUT", body: JSON.stringify(input) }),
  saveAgentPreset: (input: DbAgentPreset) => requestJson("/api/agent/presets", { method: "POST", body: JSON.stringify(input) }),
  deleteAgentPreset: (id) => requestJson(`/api/agent/presets/${encodeURIComponent(id)}`, { method: "DELETE" }),
  restoreSystemAgentDefault: (apid) => requestJson(`/api/agent/system/${encodeURIComponent(apid)}/restore`, { method: "POST" }),
  listSystemAgentDefinitions: () => requestJson("/api/agent/system"),
  runAgentTest: (input) => requestJson("/api/agent/test", { method: "POST", body: JSON.stringify(input) }),
  listAgentTestHistory: () => requestJson("/api/agent/test-history"),
  undoAgentTestSession: (id) => requestJson(`/api/agent/test-history/${encodeURIComponent(id)}/undo`, { method: "POST" }),
  regenerateAgentTestSessionReply: (id) => requestJson(`/api/agent/test-history/${encodeURIComponent(id)}/regenerate`, { method: "POST" }),
  deleteAgentTestSession: (id) => requestVoid(`/api/agent/test-history/${encodeURIComponent(id)}`, { method: "DELETE" }),
  branchAgentTestSession: (id) => requestJson(`/api/agent/test-history/${encodeURIComponent(id)}/branch`, { method: "POST" }),
};
