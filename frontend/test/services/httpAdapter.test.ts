import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { httpBackend, requestInit } from "@/services/httpAdapter";
import type { ConversationAggregateDto } from "@/types/chatTransport";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  accountSession.accept({ ...structuredClone(connectionSnapshot), client: { ...connectionSnapshot.client, connected: true }, capabilities: { read_chat: true, use_ai: true, operate_client: true } });
});

const aggregate: ConversationAggregateDto = {
  sid: 42,
  name: "Buyer session",
  participants: [],
  messages: [],
  latest: { content: "latest", updated_at: "2026-09-08 10:00" },
  unread_count: 0,
  status: "following",
  priority: "medium",
};

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("http adapter contract", () => {
  it.each([
    ["/api/settings/alibaba-data-dir", "PUT", { path: "E:\\Data" }, (epoch: string) => httpBackend.saveDataDirPath("E:\\Data", epoch)],
    ["/api/settings/ali-id", "PUT", { ali_id: "seller-b" }, (epoch: string) => httpBackend.saveAliId("seller-b", epoch)],
    ["/api/settings/ali-keys", "PUT", { ali_id: "seller-a", aes_key_hex: "test-key" }, (epoch: string) => httpBackend.saveAliKey("seller-a", "test-key", epoch)],
    ["/api/settings/ali-keys/seller-a", "DELETE", undefined, (epoch: string) => httpBackend.clearAliKey("seller-a", epoch)],
  ] as const)("settings write %s uses the explicit epoch while current requests are blocked", async (path, method, body, operation) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: {} }), { headers: { "X-Account-Epoch": "new-epoch-after-save" } }));
    globalThis.fetch = fetchMock;
    accountSession.invalidate();
    await operation("captured-before-invalidation");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(path);
    expect(init.method).toBe(method);
    expect(init.body && JSON.parse(init.body)).toEqual(body);
    expect(new Headers(init.headers).get("X-Account-Epoch")).toBe("captured-before-invalidation");
    await expect(operation("")).rejects.toThrow("账号状态已变化");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("preserves a settings write's backend 409 instead of retrying with a new token", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 1, msg: "stale settings epoch", data: null }), { status: 409, headers: { "X-Account-Epoch": "server-new" } }));
    await expect(httpBackend.saveAliId("seller-b", "old-epoch")).rejects.toMatchObject({ status: 409, message: "stale settings epoch" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("observes connection without an account and sends explicit action tokens", async () => {
    accountSession.invalidate();
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ code: 0, msg: "ok", data: connectionSnapshot }))));
    globalThis.fetch = fetchMock;
    await expect(httpBackend.getConnection()).resolves.toEqual(connectionSnapshot);
    await httpBackend.connectClient("epoch-a");
    await httpBackend.confirmClient("epoch-a", "window-a");
    accountSession.accept({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "epoch-a" } });
    await httpBackend.retryConnection("epoch-a");
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init.method ?? "GET", init.body && JSON.parse(init.body)])).toEqual([
      ["/api/settings/connection", "GET", undefined],
      ["/api/settings/connection/connect", "POST", { epoch: "epoch-a" }],
      ["/api/settings/connection/confirm", "POST", { epoch: "epoch-a", window_generation: "window-a" }],
      ["/api/settings/connection/retry", "POST", { epoch: "epoch-a" }],
    ]);
  });

  it("blocks writes and retry during suspension, then resumes only the same account", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ code: 0, msg: "ok", data: connectionSnapshot }))));
    globalThis.fetch = fetchMock;
    accountSession.suspend();
    await expect(httpBackend.retryConnection("mock-1")).rejects.toThrow("账号状态已变化");
    await expect(httpBackend.sendMessage({ conversationId: "42", content: "draft" })).rejects.toThrow("账号状态已变化");
    expect(fetchMock).not.toHaveBeenCalled();
    accountSession.accept(connectionSnapshot);
    await httpBackend.retryConnection("mock-1");
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("X-Account-Epoch")).toBe("mock-1");
    await expect(httpBackend.retryConnection("old-epoch")).rejects.toThrow("账号状态已变化");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    () => httpBackend.getSelfInfo(),
    () => httpBackend.listConversations(),
    () => httpBackend.getTranslation("hello"),
    () => httpBackend.requestTranslations({ texts: ["hello"] }),
    () => httpBackend.exportConversations({ conversationIds: ["42"] }),
    () => httpBackend.resetCache(),
  ])("attaches the expected epoch to account data requests", async (call) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: [] })));
    globalThis.fetch = fetchMock;
    await call();
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("X-Account-Epoch")).toBe("mock-1");
  });

  it("rejects account requests before fetch while a settings mutation is unresolved", async () => {
    globalThis.fetch = vi.fn();
    accountSession.invalidate();
    await expect(httpBackend.getSelfInfo()).rejects.toThrow("账号状态已变化");
    await expect(httpBackend.sendMessage({ conversationId: "42", content: "hello" })).rejects.toThrow("账号状态已变化");
    await expect(httpBackend.resetCache()).rejects.toThrow("账号状态已变化");
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["send", "test"] as const)("blocks %s before manual client confirmation", async (action) => {
    accountSession.accept({ ...structuredClone(connectionSnapshot), client: { ...connectionSnapshot.client, connected: true } });
    globalThis.fetch = vi.fn();
    await expect(httpBackend.sendMessage({ conversationId: "42", content: "hello", action })).rejects.toThrow("人工确认");
    await expect(httpBackend.gotoContact("42", "buyer")).rejects.toThrow("人工确认");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("allows read-only diagnostics on an unconfirmed connected client, including without a readable archive", async () => {
    accountSession.accept({ ...structuredClone(connectionSnapshot), client: { ...connectionSnapshot.client, connected: true, confirmed: false }, capabilities: { read_chat: false, use_ai: false, operate_client: false } });
    const receipt = { success: null, message: "queued", task_snapshot: { task_id: "diagnostic-1" } };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: receipt })));
    globalThis.fetch = fetchMock;
    await expect(httpBackend.runNodeTest()).resolves.toEqual(receipt);
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("X-Account-Epoch")).toBe("mock-1");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ entry: "ChatInput_GoToInput" });
  });

  it("rejects read-only diagnostics while disconnected", async () => {
    accountSession.accept(structuredClone(connectionSnapshot));
    globalThis.fetch = vi.fn();
    await expect(httpBackend.runNodeTest()).rejects.toThrow("接入客户端");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("allows cache reset without client confirmation and rejects a mismatched response epoch", async () => {
    accountSession.accept(structuredClone(connectionSnapshot));
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, msg: "ok", data: null }), { headers: { "X-Account-Epoch": "mock-1" } }))
      .mockResolvedValueOnce(new Response("", { headers: { "X-Account-Epoch": "other-account" } }));
    await expect(httpBackend.resetCache()).resolves.toBeUndefined();
    await expect(httpBackend.resetCache()).rejects.toThrow("账号状态已变化");
  });

  it("keeps AI availability independent of client operation", async () => {
    accountSession.accept(structuredClone(connectionSnapshot));
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: [] })));
    await expect(httpBackend.getAssistantSuggestions("42")).resolves.toEqual([]);
    accountSession.accept({ ...connectionSnapshot, capabilities: { read_chat: true, use_ai: false, operate_client: true } });
    await expect(httpBackend.analyzeConversation("42")).rejects.toThrow("AI 暂不可用");
  });

  it("discards an old response after a global epoch change", async () => {
    let respond!: (response: Response) => void;
    globalThis.fetch = vi.fn(() => new Promise<Response>((resolve) => { respond = resolve; }));
    const pending = httpBackend.getSelfInfo();
    accountSession.accept({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "epoch-b" } });
    respond(new Response(JSON.stringify({ code: 0, msg: "ok", data: { login_id: "old-seller" } })));
    await expect(pending).rejects.toThrow("账号状态已变化");
  });

  it("checks the epoch again after the response body finishes reading", async () => {
    let finish!: (text: string) => void;
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, headers: new Headers(), text: () => new Promise<string>((resolve) => { finish = resolve; }) });
    const pending = httpBackend.getSelfInfo();
    await Promise.resolve();
    accountSession.invalidate();
    finish(JSON.stringify({ code: 0, msg: "ok", data: {} }));
    await expect(pending).rejects.toThrow("账号状态已变化");
  });

  it("rejects a mismatching server epoch even before the local poll observes a switch", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: {} }), { headers: { "X-Account-Epoch": "server-new" } }));
    await expect(httpBackend.getSelfInfo()).rejects.toThrow("账号状态已变化");
  });

  it.each([false, null])("preserves queued message execution.success=%s and the conversation/message payload", async (success) => {
    const execution = { success, message: "queued", task_snapshot: { task_id: "task-1", description: "send", status: "pending", message: "queued", result: null, created_at: 0, started_at: null, completed_at: null } };
    const message = { message: { external_mid: "message-1", sid: 42, sender: 1, read: true, content: "hello", type: "text" }, created_at: "2026-09-08 10:00", role: "seller" };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: { conversation: aggregate, message, execution } })));
    globalThis.fetch = fetchMock;

    const input = { conversationId: "42", content: "hello", action: "send" as const };
    const result = await httpBackend.sendMessage(input);
    expect(result.execution).toEqual(execution);
    expect(result.conversation.id).toBe("42");
    expect(result.message).toMatchObject({ content: "hello", role: "seller" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(input);
  });

  it.each([undefined, "ChatInput_GoToInput", "ContactSearch_GoToSearch"] as const)("submits node entry %s and preserves the asynchronous receipt", async (entry) => {
    const receipt = { success: null, message: "任务已提交", task_snapshot: { task_id: "node-1", description: "node", status: "pending", message: "queued", result: null, created_at: 0, started_at: null, completed_at: null } };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: receipt })));
    globalThis.fetch = fetchMock;

    await expect(httpBackend.runNodeTest(entry)).resolves.toEqual(receipt);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/status/node-test");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ entry: entry ?? "ChatInput_GoToInput" });
  });

  it("accepts void responses without attempting to parse JSON", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, msg: "ok", data: null }), { status: 200 }));
    globalThis.fetch = fetchMock;

    await expect(httpBackend.deleteAgentTestSession("session-1")).resolves.toBeUndefined();
    await expect(httpBackend.resetCache()).resolves.toBeUndefined();
  });

  it("rejects empty bodies for JSON requests", async () => {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(new Response("", { status: 200 }))
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    await expect(httpBackend.getSelfInfo()).rejects.toThrow("API response body is empty: /api/self-info");
    await expect(httpBackend.deleteAgentPreset("agent-1")).rejects.toThrow("API response body is empty: /api/agent/presets/agent-1");
  });

  it("encodes conversation and agent path parameters", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, msg: "ok", data: aggregate }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, msg: "ok", data: [] }), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, msg: "ok", data: { apid: "agent-1", name: "Agent", description: "", prompt: "", intelevel: 0, tools: [], enabled: true, updated_at: "2026-09-11", category: "system" } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    globalThis.fetch = fetchMock;

    await httpBackend.getConversation("sid/42 with space");
    await httpBackend.getAssistantSuggestions("sid/42 with space");
    await httpBackend.restoreSystemAgentDefault("agent/id with space");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/conversations/sid%2F42%20with%20space",
      "/api/conversations/sid%2F42%20with%20space/suggestions",
      "/api/agent/system/agent%2Fid%20with%20space/restore",
    ]);
  });

  it("unwraps successful API envelopes and rejects non-zero codes", async () => {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, msg: "ok", data: { ready: true } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 7, msg: "业务失败", data: null }), { status: 200 }));

    await expect(httpBackend.getSelfInfo()).resolves.toEqual({ ready: true });
    await expect(httpBackend.getSelfInfo()).rejects.toThrow("业务失败");
  });

  it("rejects bare payloads without an envelope", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(aggregate), { status: 200 }));

    await expect(httpBackend.getConversation("42")).rejects.toThrow("API protocol error: /api/conversations/42");
  });

  it("rejects malformed API envelopes", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "0", msg: "ok", data: { ready: true } }), { status: 200 }));

    await expect(httpBackend.getSelfInfo()).rejects.toThrow("API protocol error: /api/self-info");
  });

  it("rejects error envelopes on void requests", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 7, msg: "拒绝删除" }), { status: 200 }));

    await expect(httpBackend.deleteAgentTestSession("session-1")).rejects.toThrow("拒绝删除");
  });

  it("rejects invalid JSON response bodies", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response("not-json", { status: 200 }));

    await expect(httpBackend.getSelfInfo()).rejects.toThrow("API response body is not valid JSON: /api/self-info");
  });

  it("keeps caller headers when adding JSON defaults", () => {
    const init = requestInit({ headers: { "X-Trace-Id": "trace-1" } });
    const headers = new Headers(init.headers);

    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-Trace-Id")).toBe("trace-1");
  });
});
