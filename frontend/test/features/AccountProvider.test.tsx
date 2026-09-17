// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import type { ConnectionSnapshot } from "@/types/connection";
import type { OperationsBackend } from "@/services/interfaces";
import { httpBackend } from "@/services/httpAdapter";
import { useDataDirSettings } from "@/features/settings/hooks/useDataDirSettings";

const mocks = vi.hoisted(() => ({
  message: { warning: vi.fn(), error: vi.fn() },
  backend: { getConnection: vi.fn(), getSelfInfo: vi.fn(), connectClient: vi.fn(), retryConnection: vi.fn(), confirmClient: vi.fn() },
}));
vi.mock("antd", () => ({ App: { useApp: () => ({ message: mocks.message }) } }));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));

let root: Root;
let container: HTMLDivElement;
let account: ReturnType<typeof useAccount>;
let scoped: OperationsBackend;
let directory: ReturnType<typeof useDataDirSettings>;

function Workspace() {
  const client = useAccountBackend();
  useEffect(() => { scoped = client; }, [client]);
  return <input defaultValue="" />;
}

function Probe() {
  const current = useAccount();
  const settings = useDataDirSettings();
  useEffect(() => { account = current; }, [current]);
  useEffect(() => { directory = settings; }, [settings]);
  return !current.blocked && current.snapshot ? <Workspace key={`${current.snapshot.account.epoch}:${current.generation}`} /> : null;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  mocks.backend.getConnection.mockReset().mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.getSelfInfo.mockReset().mockResolvedValue(null);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function mount() {
  await act(async () => root.render(<AccountProvider><Probe /></AccountProvider>));
}

describe("shared account provider", () => {
  it("observes initially, on focus, visibility and visible interval without connecting or syncing", async () => {
    await mount();
    expect(mocks.backend.getConnection).toHaveBeenCalledTimes(1);
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(mocks.backend.getConnection).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "hidden", { value: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(mocks.backend.getConnection).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "hidden", { value: false });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mocks.backend.getConnection).toHaveBeenCalledTimes(4);
    expect(mocks.backend.connectClient).not.toHaveBeenCalled();
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
    expect(mocks.backend.confirmClient).not.toHaveBeenCalled();
  });

  it("rejects out-of-order observations and remounts the workspace on epoch change", async () => {
    await mount();
    const input = container.querySelector("input")!;
    input.value = "old workspace";
    const slow = deferred<ConnectionSnapshot>();
    mocks.backend.getConnection.mockReturnValueOnce(slow.promise);
    let first!: ReturnType<typeof account.refresh>;
    await act(async () => { first = account.refresh(); });
    const next = { ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "b", self_ali_id: "seller-b" } };
    mocks.backend.getConnection.mockResolvedValueOnce(next);
    await act(async () => { await account.refresh(); });
    await act(async () => { slow.resolve(connectionSnapshot); await first; });
    expect(account.snapshot?.account.self_ali_id).toBe("seller-b");
    expect(container.querySelector("input")).not.toBe(input);
    expect(container.querySelector("input")?.value).toBe("");
  });

  it("invalidates same-tab requests before starting a settings write, then reads authority and notifies tabs", async () => {
    await mount();
    const oldScope = scoped;
    const write = deferred<void>();
    const oldPoll = deferred<ConnectionSnapshot>();
    mocks.backend.getConnection.mockReturnValueOnce(oldPoll.promise);
    let poll!: ReturnType<typeof account.refresh>;
    await act(async () => { poll = account.refresh(); });
    const operation = vi.fn(() => {
      expect(accountSession.get().blocked).toBe(true);
      return write.promise;
    });
    let mutation!: Promise<void>;
    await act(async () => { mutation = account.mutate(operation); });
    expect(operation).toHaveBeenCalledWith(connectionSnapshot.account.epoch);
    expect(account.mutating).toBe(true);
    expect(container.querySelector("input")).toBeNull();
    expect(localStorage.getItem("maa:account-changed")).toBeTruthy();
    await expect(oldScope.getSelfInfo()).rejects.toThrow("账号状态已变化");
    await act(async () => { oldPoll.resolve(connectionSnapshot); await poll; });
    expect(account.blocked).toBe(true);
    await act(async () => { write.resolve(); await mutation; });
    expect(account.blocked).toBe(false);
    expect(account.mutating).toBe(false);
    await expect(oldScope.getSelfInfo()).rejects.toThrow("账号状态已变化");
    expect(mocks.backend.getSelfInfo).not.toHaveBeenCalled();
  });

  it.each([
    ["/api/settings/alibaba-data-dir", (epoch: string) => httpBackend.saveDataDirPath("E:\\Data", epoch)],
    ["/api/settings/ali-id", (epoch: string) => httpBackend.saveAliId("seller-b", epoch)],
    ["/api/settings/ali-keys", (epoch: string) => httpBackend.saveAliKey("seller-a", "test-key", epoch)],
    ["/api/settings/ali-keys/seller-a", (epoch: string) => httpBackend.clearAliKey("seller-a", epoch)],
  ] as const)("sends the pre-invalidation epoch for %s even if current state changes before fetch", async (path, operation) => {
    await mount();
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: {} }), { headers: { "X-Account-Epoch": "new-epoch" } }));
    vi.stubGlobal("fetch", fetchMock);
    await act(async () => {
      await account.mutate(async (epoch) => {
        expect(accountSession.get().blocked).toBe(true);
        accountSession.accept({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "new-epoch" } });
        return operation(epoch);
      });
    });
    expect(fetchMock.mock.calls[0][0]).toBe(path);
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get("X-Account-Epoch")).toBe(connectionSnapshot.account.epoch);
  });

  it("disables directory saving and refuses mutation until an authoritative snapshot is available", async () => {
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("offline"));
    await mount();
    expect(directory.canSave).toBe(false);
    const operation = vi.fn();
    await expect(account.mutate(operation)).rejects.toThrow("账号状态已变化");
    expect(operation).not.toHaveBeenCalled();
    await act(async () => { await account.refresh(); });
    expect(directory.canSave).toBe(true);
  });

  it("refreshes after a failed mutation and stays blocked when authority cannot be read", async () => {
    await mount();
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      await expect(account.mutate(async () => { throw new Error("write failed"); })).rejects.toThrow("write failed");
    });
    expect(account.blocked).toBe(true);
    expect(account.error).toBe("offline");
    expect(container.querySelector("input")).toBeNull();
    await act(async () => { await account.refresh(); });
    expect(account.blocked).toBe(false);
  });

  it("uses storage only as a notification and reads the authoritative account", async () => {
    await mount();
    const next = deferred<ConnectionSnapshot>();
    mocks.backend.getConnection.mockReturnValueOnce(next.promise);
    await act(async () => window.dispatchEvent(new StorageEvent("storage", { key: "maa:account-changed", newValue: "untrusted-account" })));
    expect(account.blocked).toBe(true);
    await act(async () => next.resolve({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "b", self_ali_id: "authoritative" } }));
    expect(account.snapshot?.account.self_ali_id).toBe("authoritative");
    expect(account.blocked).toBe(false);
  });

  it("warns on unavailable storage without aborting a settings write", async () => {
    await mount();
    vi.stubGlobal("localStorage", { setItem: () => { throw new Error("storage denied"); } });
    await act(async () => { await account.mutate(async () => "saved"); });
    expect(account.blocked).toBe(false);
    expect(mocks.message.warning).toHaveBeenCalled();
  });
});
