import type { DbAgentPreset, DocumentLlmConfig } from "@/types/agent";
import { adaptConversationDetail, adaptConversationSummary, adaptSendMessageResult } from "@/services/chatAdapter";
import type { ConversationAggregateDto, ConversationSendResultDto } from "@/types/chatTransport";
import type { ApiResponse } from "@/types/common";
import type { OperationsBackend } from "./interfaces";

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

function timeoutSignal(init?: RequestInit): RequestInit {
  if (init?.signal) return init;
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    return { ...init, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) };
  }
  return init ?? {};
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, requestInit(timeoutSignal(init)));
  } catch (error) {
    throw new ApiError(`API request failed: ${path}`, path, { cause: error });
  }

  const text = await response.text();
  if (!response.ok) {
    throw new ApiError(envelopeMessage(text, path), path, { status: response.status });
  }

  if (!text.trim()) throw new ApiError(`API response body is empty: ${path}`, path);
  return parseApiResponse<T>(parseJsonPayload(text, path), path);
}

function envelopeMessage(text: string, path: string): string {
  try {
    const payload = JSON.parse(text) as Partial<ApiResponse<unknown>>;
    if (isPlainObject(payload) && typeof payload.msg === "string" && payload.msg) return payload.msg;
  } catch {
    // fall through
  }
  return `API request failed: ${path}`;
}

async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  let response: Response;
  try {
    response = await fetch(path, requestInit(timeoutSignal(init)));
  } catch (error) {
    throw new ApiError(`API request failed: ${path}`, path, { cause: error });
  }
  const text = await response.text();
  if (!response.ok) {
    throw new ApiError(envelopeMessage(text, path), path, { status: response.status });
  }
  if (!text.trim()) return;
  parseApiResponse<unknown>(parseJsonPayload(text, path), path);
}

export function requestInit(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return { ...init, headers };
}

export const httpBackend: OperationsBackend = {
  getSelfInfo: () => requestJson("/api/self-info"),
  resetCache: () => requestVoid("/api/cache/reset", { method: "POST" }),
  shutdownApp: () => requestVoid("/api/app/shutdown", { method: "POST" }),

  requestTranslations: (input) => requestJson("/api/messages/translations", { method: "POST", body: JSON.stringify(input) }),
  getTranslation: (text) => requestJson(`/api/messages/translations/${encodeURIComponent(text)}`),

  listConversations: async () => {
    const aggregates = await requestJson<ConversationAggregateDto[]>("/api/conversations");
    return aggregates.map(adaptConversationSummary);
  },
  getConversation: async (id) => {
    const aggregate = await requestJson<ConversationAggregateDto>(`/api/conversations/${encodeURIComponent(id)}`);
    return adaptConversationDetail(aggregate);
  },
  translateMessage: (input) => requestJson("/api/messages/translate", { method: "POST", body: JSON.stringify(input) }),
  regenerateTranslation: (input) => requestJson("/api/messages/retranslate", { method: "POST", body: JSON.stringify(input) }),
  getAssistantSuggestions: (conversationId) => requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/suggestions`),
  analyzeConversation: (conversationId) => requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/analysis`),
  sendMessage: async (input) => {
    const result = await requestJson<ConversationSendResultDto>(`/api/conversations/${encodeURIComponent(input.conversationId)}/messages`, { method: "POST", body: JSON.stringify(input) });
    return adaptSendMessageResult(result);
  },
  exportConversations: (input) => requestJson("/api/conversations/export", { method: "POST", body: JSON.stringify(input) }),
  gotoContact: (conversationId, loginId) => requestJson(`/api/conversations/${encodeURIComponent(conversationId)}/goto-contact`, { method: "POST", body: JSON.stringify({ login_id: loginId }) }),

  checkUserStatus: () => requestJson("/api/status/user"),
  checkMitmProxy: () => requestJson("/api/status/mitm-proxy"),
  checkMitmReceiver: () => requestJson("/api/status/mitm-receiver"),
  runNodeTest: (entry) => requestJson("/api/status/node-test", { method: "POST", body: JSON.stringify({ entry: entry ?? "ChatInput" }) }),
  listTaskSnapshots: () => requestJson("/api/status/tasks"),
  getSystemStatus: () => requestJson("/api/status"),
  refreshSystemStatus: () => requestJson("/api/status/refresh", { method: "POST" }),
  createTestTask: (input) => requestJson("/api/status/test-tasks", { method: "POST", body: JSON.stringify(input) }),

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
