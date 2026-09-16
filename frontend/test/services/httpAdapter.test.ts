import { afterEach, describe, expect, it, vi } from "vitest";
import { httpBackend, requestInit } from "@/services/httpAdapter";
import type { ConversationAggregateDto } from "@/types/chatTransport";

const originalFetch = globalThis.fetch;

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
