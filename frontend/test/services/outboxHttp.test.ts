import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { httpBackend } from "@/services/httpAdapter";
import { accountSession, scopeBackend } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import { outboxTask, screenshotPng } from "@/test/support/outboxFixture";

const ready = () => ({ ...structuredClone(connectionSnapshot), capabilities: { read_chat: true, use_ai: true, operate_client: true } });
beforeEach(() => { accountSession.accept(ready()); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const response = (data: unknown, epoch = "mock-1") => new Response(JSON.stringify({ code: 0, msg: "ok", data }), { headers: { "X-Account-Epoch": epoch } });

describe("outbox HTTP contract and account isolation", () => {
  it.each([
    ["/api/conversations/42%2Fspace/outbox", "GET", undefined, () => httpBackend.listOutbox("42/space")],
    ["/api/outbox/task%2Fid", "GET", undefined, () => httpBackend.getOutbox("task/id")],
    ["/api/outbox/task%2Fid/confirm", "POST", { version: 4, screenshot_id: "frame-a" }, () => httpBackend.confirmOutbox("task/id", 4, "frame-a")],
    ["/api/outbox/task%2Fid/cancel", "POST", { version: 4 }, () => httpBackend.cancelOutbox("task/id", 4)],
    ["/api/outbox/task%2Fid/retry", "POST", { version: 4 }, () => httpBackend.retryOutbox("task/id", 4)],
  ] as const)("encodes and scopes %s", async (path, method, body, call) => {
    const data = method === "GET" && path.endsWith("outbox") ? [outboxTask()] : outboxTask();
    const fetch = vi.fn().mockResolvedValue(response(data));
    vi.stubGlobal("fetch", fetch);
    await expect(call()).resolves.toEqual(data);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe(path);
    expect(init.method ?? "GET").toBe(method);
    expect(init.body ? JSON.parse(init.body) : undefined).toEqual(body);
    expect(new Headers(init.headers).get("X-Account-Epoch")).toBe("mock-1");
    if (method === "GET") expect(init.cache).toBe("no-store");
  });

  it("requires the stage-one operate_client gate for confirm and retry but permits disconnected reads and cancel", async () => {
    accountSession.accept(structuredClone(connectionSnapshot));
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(response(outboxTask())));
    vi.stubGlobal("fetch", fetch);
    await expect(httpBackend.confirmOutbox("task", 1, "frame")).rejects.toThrow("人工确认");
    await expect(httpBackend.retryOutbox("task", 1)).rejects.toThrow("人工确认");
    expect(fetch).not.toHaveBeenCalled();
    await httpBackend.getOutbox("task");
    await httpBackend.cancelOutbox("task", 1);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("rejects an empty idempotency key before network submission", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(httpBackend.sendMessage({ conversationId: "42", content: "hello", action: "send", idempotency_key: "" })).rejects.toThrow("幂等键");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("fetches actual PNG bytes with an epoch header and no-store, without exposing a direct image URL", async () => {
    const png = screenshotPng();
    const fetch = vi.fn().mockResolvedValue(new Response(png, { headers: { "Content-Type": "image/png", "X-Account-Epoch": "mock-1", "Cache-Control": "no-store" } }));
    vi.stubGlobal("fetch", fetch);
    const result = await httpBackend.getOutboxScreenshot("task/id", "frame/id", 4);
    expect(new Uint8Array(await result.arrayBuffer())).toEqual(new Uint8Array(await png.arrayBuffer()));
    expect(fetch.mock.calls[0][0]).toBe("/api/outbox/task%2Fid/screenshot/frame%2Fid?version=4");
    expect(fetch.mock.calls[0][1].cache).toBe("no-store");
    expect(new Headers(fetch.mock.calls[0][1].headers).get("X-Account-Epoch")).toBe("mock-1");
  });

  it.each([undefined, "another-seller"])("rejects screenshot responses with epoch %s", async (epoch) => {
    const headers = new Headers({ "Content-Type": "image/png" });
    if (epoch) headers.set("X-Account-Epoch", epoch);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(screenshotPng(), { headers })));
    await expect(httpBackend.getOutboxScreenshot("task", "frame", 1)).rejects.toThrow("账号状态已变化");
  });

  it("rejects non-PNG responses even with the right account epoch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>sign in</html>", { headers: { "Content-Type": "text/html", "X-Account-Epoch": "mock-1" } })));
    await expect(httpBackend.getOutboxScreenshot("task", "frame", 1)).rejects.toThrow("截图格式无效");
  });

  it("rechecks account scope after the PNG body finishes reading", async () => {
    let finish!: (blob: Blob) => void;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, headers: new Headers({ "Content-Type": "image/png", "X-Account-Epoch": "mock-1" }), blob: () => new Promise<Blob>((resolve) => { finish = resolve; }) }));
    const pending = httpBackend.getOutboxScreenshot("task", "frame", 1);
    await Promise.resolve();
    accountSession.invalidate();
    finish(screenshotPng());
    await expect(pending).rejects.toThrow("账号状态已变化");
  });

  it("discards late old-account tasks and prevents an old chain from confirming in a new account", async () => {
    let finish!: (result: Response) => void;
    const fetch = vi.fn().mockImplementation(() => new Promise<Response>((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", fetch);
    const scoped = scopeBackend(httpBackend);
    const pending = scoped.getOutbox("task");
    accountSession.accept({ ...ready(), account: { ...connectionSnapshot.account, epoch: "b" } });
    finish(response(outboxTask()));
    await expect(pending).rejects.toThrow("账号状态已变化");
    await expect(scoped.confirmOutbox("task", 1, "frame")).rejects.toThrow("账号状态已变化");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not repeat a rejected confirmation or retry request", async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ code: 1, msg: "version changed", data: null }), { status: 409, headers: { "X-Account-Epoch": "mock-1" } })));
    vi.stubGlobal("fetch", fetch);
    await expect(httpBackend.confirmOutbox("task", 1, "frame")).rejects.toMatchObject({ status: 409 });
    await expect(httpBackend.retryOutbox("task", 1)).rejects.toMatchObject({ status: 409 });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
