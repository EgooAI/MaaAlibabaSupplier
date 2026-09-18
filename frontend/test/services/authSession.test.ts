// @vitest-environment happy-dom
import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authSession, AUTH_STORAGE_KEY, captureAuth } from "@/services/authSession";
import { accountSession, scopeBackend } from "@/services/accountSession";
import { httpBackend } from "@/services/httpAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import { authenticatedSession, memoryLocks, memoryStorage } from "@/test/support/authFixture";

const response = (data: unknown, status = 200, headers?: HeadersInit) => new Response(JSON.stringify({ code: status === 200 ? 0 : 1, msg: "result", data }), { status, headers });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(async () => {
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("navigator", { locks: memoryLocks() });
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("fetch", vi.fn());
  // Move past any previous rate-limit deadline without sleeping.
  vi.spyOn(Date, "now").mockReturnValue(Math.max(Date.now(), authSession.get().retryAt + 1));
  await authSession.bootstrap();
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("authentication lifetime", () => {
  it("blocks business calls until a persisted permanent token is verified and discards cached account data", async () => {
    accountSession.accept(connectionSnapshot);
    localStorage.setItem(AUTH_STORAGE_KEY, "permanent-token");
    const pending = deferred<Response>();
    vi.mocked(fetch).mockReturnValue(pending.promise);
    const bootstrap = authSession.bootstrap();
    expect(accountSession.get().snapshot).toBeNull();
    await expect(httpBackend.getConnection()).rejects.toThrow("登录状态已变化");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer permanent-token");
    pending.resolve(response({ authenticated: true }));
    await bootstrap;
    expect(captureAuth().token).toBe("permanent-token");
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("permanent-token");
    vi.mocked(fetch).mockResolvedValue(response({ authenticated: true }));
    await authSession.bootstrap();
    expect(captureAuth().token).toBe("permanent-token");
  });

  it("hashes exact UTF-8 including whitespace, persists only the opaque token, and does not send it to login", async () => {
    const secret = "  密钥\n";
    vi.mocked(fetch).mockResolvedValue(response({ token: "opaque-login-token" }));
    await authSession.login(secret);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(init!.body as string)).toEqual({ secret_sha256: createHash("sha256").update(secret, "utf8").digest("hex") });
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
    expect(localStorage.length).toBe(1);
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("opaque-login-token");
    expect(authSession.get().phase).toBe("authenticated");
  });

  it("refuses insecure contexts without sending the secret", async () => {
    vi.stubGlobal("isSecureContext", false);
    await authSession.login("secret");
    expect(fetch).not.toHaveBeenCalled();
    expect(authSession.get()).toMatchObject({ phase: "signedOut", error: expect.stringContaining("HTTPS") });
  });

  it.each(["3", new Date(Date.now() + 60_000).toUTCString()])("respects login 429 Retry-After %s without automatic replay", async (retry) => {
    vi.mocked(fetch).mockResolvedValue(response(null, 429, { "Retry-After": retry }));
    await authSession.login("secret");
    expect(authSession.get()).toMatchObject({ phase: "signedOut", error: expect.stringContaining("频繁") });
    expect(authSession.get().retryAt).toBeGreaterThan(Date.now());
    await authSession.login("secret");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([429, 500, "network"])("retains bootstrap credentials on %s and supports explicit retry", async (failure) => {
    localStorage.setItem(AUTH_STORAGE_KEY, "saved");
    if (failure === "network") vi.mocked(fetch).mockRejectedValueOnce(new Error("offline"));
    else vi.mocked(fetch).mockResolvedValueOnce(response(null, failure as number, { "Retry-After": "2" }));
    await authSession.bootstrap();
    expect(authSession.get().phase).toBe("retry");
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("saved");
    expect(() => captureAuth()).toThrow();
    vi.mocked(Date.now).mockReturnValue(Math.max(Date.now(), authSession.get().retryAt + 1));
    vi.mocked(fetch).mockResolvedValueOnce(response({ authenticated: true }));
    await authSession.retry();
    expect(captureAuth().token).toBe("saved");
  });

  it("removes only the auth key on bootstrap 401", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, "revoked");
    localStorage.setItem("maa:draft", "draft");
    localStorage.setItem("maa:intent", "intent");
    vi.mocked(fetch).mockResolvedValue(new Response("", { status: 401 }));
    await authSession.bootstrap();
    expect(authSession.get().phase).toBe("signedOut");
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem("maa:draft")).toBe("draft");
    expect(localStorage.getItem("maa:intent")).toBe("intent");
  });

  it("makes storage denial explicit and requires opting into a temporary session", async () => {
    vi.spyOn(localStorage, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new Error("denied"); });
    await authSession.bootstrap();
    expect(authSession.get().warning).toContain("无法读取");
    vi.mocked(fetch).mockResolvedValue(response({ token: "temporary" }));
    await authSession.login("secret");
    expect(authSession.get().phase).toBe("persist");
    expect(() => captureAuth()).toThrow();
    await authSession.persist();
    expect(authSession.get().phase).toBe("persist");
    authSession.useTemporarySession();
    expect(captureAuth().token).toBe("temporary");
    expect(authSession.get().temporary).toBe(true);
  });

  it("can retry storage persistence without logging in again", async () => {
    const denied = vi.spyOn(localStorage, "setItem").mockImplementationOnce(() => { throw new Error("quota"); });
    vi.mocked(fetch).mockResolvedValue(response({ token: "saved-after-retry" }));
    await authSession.login("secret");
    expect(authSession.get().phase).toBe("persist");
    await authSession.persist();
    expect(denied).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(captureAuth().token).toBe("saved-after-retry");
  });

  it("validates other-tab tokens and handles removal, clear and stale storage events", async () => {
    await authenticatedSession("first");
    const old = captureAuth();
    const pending = deferred<Response>();
    vi.mocked(fetch).mockReturnValue(pending.promise);
    localStorage.setItem(AUTH_STORAGE_KEY, "second");
    const change = authSession.onStorage(new StorageEvent("storage", { key: AUTH_STORAGE_KEY, newValue: "stale-event-value" }));
    expect(old.signal.aborted).toBe(true);
    expect(() => captureAuth()).toThrow();
    pending.resolve(response({ authenticated: true }));
    await change;
    expect(captureAuth().token).toBe("second");
    localStorage.removeItem(AUTH_STORAGE_KEY);
    await authSession.onStorage(new StorageEvent("storage", { key: AUTH_STORAGE_KEY }));
    expect(authSession.get().phase).toBe("signedOut");
    await authenticatedSession("third");
    localStorage.clear();
    await authSession.onStorage(new StorageEvent("storage", { key: null }));
    expect(authSession.get().phase).toBe("signedOut");
    expect(accountSession.get().snapshot).toBeNull();
  });

  it("does not let a late bootstrap or login response replace a newer login", async () => {
    const slow = deferred<Response>();
    localStorage.setItem(AUTH_STORAGE_KEY, "old");
    vi.mocked(fetch).mockReturnValueOnce(slow.promise);
    const boot = authSession.bootstrap();
    await authenticatedSession("new");
    slow.resolve(new Response("", { status: 401 }));
    await boot;
    expect(captureAuth().token).toBe("new");
    localStorage.removeItem(AUTH_STORAGE_KEY);
    await authSession.bootstrap();
    const lateLogin = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(lateLogin.promise);
    const login = authSession.login("secret");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    await authenticatedSession("newest");
    lateLogin.resolve(response({ token: "late" }));
    await login;
    expect(captureAuth().token).toBe("newest");
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("newest");
  });

  it("cancels an in-flight login when another tab removes authentication", async () => {
    const pending = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    const login = authSession.login("secret");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await authSession.onStorage(new StorageEvent("storage", { key: AUTH_STORAGE_KEY, newValue: null }));
    pending.resolve(response({ token: "late-login" }));
    await login;
    expect(authSession.get().phase).toBe("signedOut");
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("reports logout storage failure while still revoking the server token", async () => {
    await authenticatedSession();
    vi.spyOn(localStorage, "removeItem").mockImplementation(() => { throw new Error("denied"); });
    vi.mocked(fetch).mockResolvedValue(response(null));
    await authSession.logout();
    expect(authSession.get()).toMatchObject({ phase: "signedOut", warning: expect.stringContaining("无法安全清除") });
    expect(fetch).toHaveBeenCalledOnce();
  });
});

describe("cross-tab auth storage coordination", () => {
  async function otherTab() {
    vi.resetModules();
    const { authSession: session } = await import("@/services/authSession");
    await session.bootstrap();
    return session;
  }

  async function holdStorageLock() {
    const release = deferred<void>();
    const acquired = deferred<void>();
    const held = navigator.locks.request(AUTH_STORAGE_KEY, () => {
      acquired.resolve();
      return release.promise;
    });
    await acquired.promise;
    return async () => { release.resolve(); await held; };
  }

  it.each(["logout", "401"])("preserves another tab's queued newer token during %s, without delaying local invalidation", async (reason) => {
    const tabB = await otherTab();
    await authenticatedSession("T1");
    const ticket = captureAuth();
    accountSession.accept(connectionSnapshot);
    const release = await holdStorageLock();
    const requests = vi.mocked(navigator.locks.request);
    const before = requests.mock.calls.length;
    vi.mocked(fetch).mockImplementation((url) => Promise.resolve(response(url === "/api/auth/login" ? { token: "T2" } : null)));
    const login = tabB.login("secret");
    await vi.waitFor(() => expect(requests).toHaveBeenCalledTimes(before + 1));
    const stop = reason === "logout" ? authSession.logout() : authSession.unauthorized(ticket.generation);
    expect(requests).toHaveBeenCalledTimes(before + 2);
    expect(authSession.get().phase).toBe("signedOut");
    expect(ticket.signal.aborted).toBe(true);
    expect(accountSession.get().snapshot).toBeNull();
    await expect(httpBackend.shutdownApp()).rejects.toThrow("登录状态已变化");
    if (reason === "logout") expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toContain("/api/auth/logout");
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("T1");
    await release();
    await Promise.all([login, stop]);
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("T2");
    expect(tabB.get().phase).toBe("authenticated");
    expect(requests.mock.calls.every(([name]) => name === AUTH_STORAGE_KEY)).toBe(true);
  });

  it("queues another tab's persistence between the removal's read and delete, then preserves that write", async () => {
    const tabB = await otherTab();
    const setItem = localStorage.setItem.bind(localStorage);
    vi.spyOn(localStorage, "setItem").mockImplementationOnce(() => { throw new Error("quota"); });
    vi.mocked(fetch).mockResolvedValue(response({ token: "T2" }));
    await tabB.login("secret");
    expect(tabB.get().phase).toBe("persist");
    await authenticatedSession("T1");
    const getItem = localStorage.getItem.bind(localStorage);
    let competingWrite: Promise<void> | undefined;
    const events: string[] = [];
    const removeItem = localStorage.removeItem.bind(localStorage);
    vi.mocked(localStorage.setItem).mockImplementation((key, value) => { events.push(`write ${value}`); setItem(key, value); });
    vi.spyOn(localStorage, "removeItem").mockImplementation((key) => { events.push("delete"); removeItem(key); });
    vi.spyOn(localStorage, "getItem").mockImplementation((key) => {
      const value = getItem(key);
      if (!competingWrite && key === AUTH_STORAGE_KEY) {
        events.push(`read ${value}`);
        competingWrite = tabB.persist();
        expect(tabB.get().phase).toBe("persist");
      }
      return value;
    });
    await authSession.unauthorized(captureAuth().generation);
    await competingWrite;
    expect(events).toEqual(["read T1", "delete", "write T2"]);
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("T2");
    expect(tabB.get().phase).toBe("authenticated");
  });

  it("skips a queued removal after the local generation changes, even when the token is unchanged", async () => {
    await authenticatedSession("T1");
    const release = await holdStorageLock();
    const stop = authSession.unauthorized(captureAuth().generation);
    vi.mocked(fetch).mockResolvedValue(response({ authenticated: true }));
    await authSession.bootstrap();
    const current = captureAuth();
    const remove = vi.spyOn(localStorage, "removeItem");
    await release();
    await stop;
    current.assertCurrent();
    expect(remove).not.toHaveBeenCalled();
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("T1");
  });

  it.each(["logout", "temporary"])("does not persist a queued login after choosing %s", async (choice) => {
    const release = await holdStorageLock();
    vi.mocked(fetch).mockImplementation((url) => Promise.resolve(response(url === "/api/auth/login" ? { token: "T1" } : null)));
    const requests = vi.mocked(navigator.locks.request);
    const before = requests.mock.calls.length;
    const login = authSession.login("secret");
    await vi.waitFor(() => expect(requests).toHaveBeenCalledTimes(before + 1));
    let stop: Promise<void> | undefined;
    if (choice === "logout") stop = authSession.logout();
    else authSession.useTemporarySession();
    await release();
    await Promise.all([login, stop]);
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(authSession.get().phase).toBe(choice === "logout" ? "signedOut" : "authenticated");
    expect(authSession.get().temporary).toBe(choice === "temporary");
  });

  it("offers temporary sessions without reading, writing or deleting shared credentials when Web Locks are unavailable", async () => {
    vi.stubGlobal("navigator", {});
    localStorage.setItem(AUTH_STORAGE_KEY, "existing");
    const read = vi.spyOn(localStorage, "getItem");
    const write = vi.spyOn(localStorage, "setItem");
    const remove = vi.spyOn(localStorage, "removeItem");
    await authSession.bootstrap();
    expect(authSession.get()).toMatchObject({ phase: "signedOut", warning: expect.stringContaining("Web Locks") });
    expect(read).not.toHaveBeenCalled();
    vi.mocked(fetch).mockImplementation((url) => Promise.resolve(response(url === "/api/auth/login" ? { token: "temporary" } : null)));
    await authSession.login("secret");
    expect(authSession.get()).toMatchObject({ phase: "persist", error: expect.stringContaining("Web Locks") });
    expect(() => captureAuth()).toThrow();
    authSession.useTemporarySession();
    expect(captureAuth().token).toBe("temporary");
    await authSession.logout();
    expect(authSession.get().warning).toContain("无法安全清除");
    expect(write).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("existing");
  });

  it("does not claim persistence succeeded when storage silently drops the write", async () => {
    vi.spyOn(localStorage, "setItem").mockImplementation(() => {});
    vi.mocked(fetch).mockResolvedValue(response({ token: "dropped" }));
    await authSession.login("secret");
    expect(authSession.get()).toMatchObject({ phase: "persist", error: expect.stringContaining("无法保存") });
    expect(() => captureAuth()).toThrow();
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });
});

const transports = [
  ["json", () => httpBackend.getSelfInfo()],
  ["void", () => httpBackend.resetCache()],
  ["png", () => httpBackend.getOutboxScreenshot("task", "frame", 1)],
] as const;
describe("authenticated business transport", () => {
  beforeEach(async () => {
    await authenticatedSession();
    accountSession.accept(connectionSnapshot);
  });

  it.each(transports)("attaches bearer on %s", async (mode, call) => {
    vi.mocked(fetch).mockResolvedValue(mode === "png"
      ? new Response(new Blob(["png"]), { headers: { "Content-Type": "image/png", "X-Account-Epoch": "mock-1" } })
      : response(null));
    await call();
    const init = vi.mocked(fetch).mock.calls[0][1];
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer test-session");
    expect(new Headers(init?.headers).get("X-Account-Epoch")).toBe("mock-1");
  });

  it.each(transports)("handles current 401 before the account guard on %s", async (_mode, call) => {
    const pending = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    const request = call();
    accountSession.invalidate();
    pending.resolve(new Response("", { status: 401 }));
    await expect(request).rejects.toThrow("登录状态已变化");
    expect(authSession.get().phase).toBe("signedOut");
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it.each(transports)("ignores old 401 after a new login on %s", async (_mode, call) => {
    const pending = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise);
    const request = call();
    await authenticatedSession("replacement");
    pending.resolve(new Response("", { status: 401 }));
    await expect(request).rejects.toThrow("登录状态已变化");
    expect(captureAuth().token).toBe("replacement");
  });

  it.each(transports)("rejects a late %s body after a new login", async (mode, call) => {
    const body = deferred<string | Blob>();
    vi.mocked(fetch).mockResolvedValue({ ok: true, status: 200, headers: new Headers({ "Content-Type": "image/png", "X-Account-Epoch": "mock-1" }), text: () => body.promise, blob: () => body.promise } as Response);
    const request = call();
    await Promise.resolve();
    await authenticatedSession("replacement");
    body.resolve(mode === "png" ? new Blob(["old frame"]) : JSON.stringify({ code: 0, msg: "ok", data: null }));
    await expect(request).rejects.toThrow("登录状态已变化");
  });

  it("aborts locally before server logout, warns on revoke failure, and prevents old mutation chains", async () => {
    const pending = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(pending.promise).mockRejectedValueOnce(new Error("offline"));
    const scoped = scopeBackend(httpBackend);
    const request = scoped.getSelfInfo();
    const init = vi.mocked(fetch).mock.calls[0][1];
    localStorage.setItem("maa:draft", "keep");
    const logout = authSession.logout();
    expect(init?.signal?.aborted).toBe(true);
    expect(accountSession.get().snapshot).toBeNull();
    await expect(httpBackend.shutdownApp()).rejects.toThrow("登录状态已变化");
    await logout;
    expect(authSession.get().warning).toContain("撤销凭证失败");
    expect(localStorage.getItem("maa:draft")).toBe("keep");
    expect(vi.mocked(fetch).mock.calls[1][0]).toBe("/api/auth/logout");
    expect(new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers).get("Authorization")).toBe("Bearer test-session");
    await authenticatedSession("replacement");
    accountSession.accept(connectionSnapshot);
    pending.resolve(response(null));
    await expect(request).rejects.toThrow();
    await expect(scoped.resetCache()).rejects.toThrow("账号状态已变化");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it.each([429, 500, "network"])("keeps a verified credential on business %s without replay", async (failure) => {
    if (failure === "network") vi.mocked(fetch).mockRejectedValue(new Error("offline"));
    else vi.mocked(fetch).mockResolvedValue(response(null, failure as number));
    await expect(httpBackend.resetCache()).rejects.toThrow();
    expect(captureAuth().token).toBe("test-session");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
