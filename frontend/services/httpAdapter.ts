import type { DbAgentPreset, DocumentLlmConfig } from "@/types/agent";
import type { ApiResponse } from "@/types/common";
import { adaptConversationDetail, adaptConversationSummary, adaptSendMessageResult } from "@/services/chatAdapter";
import type { ConversationAggregateDto, ConversationSendResultDto } from "@/types/chatTransport";
import type { OperationsBackend } from "./interfaces";

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function hasEnvelopeShape(value: unknown): value is Record<string, unknown> {
  return isPlainObject(value) && ("code" in value || "msg" in value || "message" in value || "data" in value);
}

function parseApiResponse<T>(payload: unknown, path: string): ApiResponse<T> {
  if (!hasEnvelopeShape(payload)) throw new Error(`API protocol error: ${path}`);
  const message = "msg" in payload ? payload.msg : payload.message;
  if (typeof payload.code !== "number" || typeof message !== "string") {
    throw new Error(`API protocol error: ${path}`);
  }
  if (payload.code !== 0) throw new Error(message);
  return { code: payload.code, msg: message, data: payload.data as T };
}

function parseJsonPayload(text: string, path: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new Error(`API response body is not valid JSON: ${path}`);
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, requestInit(init));

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status} ${response.statusText}`);
  }

  const text = await response.text();
  if (!text.trim()) throw new Error(`API response body is empty: ${path}`);
  const payload = parseJsonPayload(text, path);
  if (hasEnvelopeShape(payload)) return parseApiResponse<T>(payload, path).data;
  return payload as T;
}

async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  const response = await fetch(path, requestInit(init));
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status} ${response.statusText}`);
  }
  const text = await response.text();
  if (!text.trim()) return;
  const payload = parseJsonPayload(text, path);
  if (hasEnvelopeShape(payload)) parseApiResponse<unknown>(payload, path);
}

export function requestInit(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return { ...init, headers };
}

export const httpBackend: OperationsBackend = {
  getSelfInfo: () => requestJson("/api/self-info"),
  resetCache: () => requestVoid("/api/cache/reset", { method: "POST" }),

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
