// @vitest-environment happy-dom
import { act, StrictMode, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useStatusWorkbench } from "@/features/status/hooks/useStatusWorkbench";
import { useShutdownApp } from "@/features/settings/hooks/useShutdownApp";
import { ApiError } from "@/services/httpAdapter";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import type { SystemStatusSnapshot } from "@/types/status";

const mocks = vi.hoisted(() => ({
  backend: { getSystemStatus: vi.fn(), createTestTask: vi.fn(), runNodeTest: vi.fn(), sweepCards: vi.fn(), shutdownApp: vi.fn() },
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => ({ App: { useApp: () => ({ message: mocks.message }) } }));
let root: Root;
let container: HTMLDivElement;
let status: ReturnType<typeof useStatusWorkbench>;
let shutdown: ReturnType<typeof useShutdownApp>;
function StatusHarness() {
  const current = useStatusWorkbench();
  useEffect(() => { status = current; }, [current]);
  return <div>
    {current.lastFailure?.message}
    <button data-action="refresh" onClick={() => void current.refresh()}>Refresh</button>
    <button data-action="createTestTask" onClick={() => void current.createTestTask()}>Create</button>
    <button data-action="runNodeTest" onClick={() => void current.runNodeTest("ChatInput_GoToInput")}>Test input</button>
    <button data-action="testSearch" onClick={() => void current.runNodeTest("ContactSearch_GoToSearch")}>Test search</button>
    <button data-action="sweepCards" onClick={() => void current.sweepCards()}>Sweep</button>
  </div>;
}
function ShutdownHarness() {
  const current = useShutdownApp();
  useEffect(() => { shutdown = current; }, [current]);
  return <div>{current.shutdownError}</div>;
}
const snapshot: SystemStatusSnapshot = { context: { ...connectionSnapshot.account, epoch: "first" }, updatedAt: "last success", modules: [], tasks: [] };
const actions = [
  ["createTestTask", "creatingTask", "success"],
  ["runNodeTest", "testingNode", "info"],
  ["sweepCards", "sweepingCards", "info"],
] as const;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const button = (action: string) => container.querySelector<HTMLButtonElement>(`[data-action="${action}"]`)!;
const selectAccount = (epoch: string) => accountSession.accept({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch } });
const setHidden = (hidden: boolean) => {
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden });
  document.dispatchEvent(new Event("visibilitychange"));
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  accountSession.clear();
  selectAccount("first");
  mocks.backend.getSystemStatus.mockResolvedValue(snapshot);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("serializes slow observations and preserves the last snapshot with a visible failure", async () => {
  const pending = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(pending.promise);
  await act(async () => root.render(<StrictMode><StatusHarness /></StrictMode>));
  expect(status.loading).toBe(true);
  expect(status.refreshing).toBe(true);
  await act(async () => {
    button("refresh").click();
    window.dispatchEvent(new Event("focus"));
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(snapshot));
  expect(status.snapshot).toEqual(snapshot);
  expect(status.loading).toBe(false);
  expect(status.refreshing).toBe(false);
  mocks.backend.getSystemStatus.mockRejectedValueOnce(new ApiError("network lost", "/api/status", { kind: "transport", requestId: "poll-2" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(status.snapshot).toEqual(snapshot);
  expect(status.lastFailure?.at).toBe(Date.now());
  expect(container.textContent).toContain("poll-2");
  mocks.backend.getSystemStatus.mockResolvedValueOnce({ ...snapshot, updatedAt: "recovered" });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(status.snapshot?.updatedAt).toBe("recovered");
  expect(status.lastFailure).toBeUndefined();
});

it("does not treat an in-flight poll as confirmation of a completed action", async () => {
  await act(async () => root.render(<StatusHarness />));
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);

  const poll = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(poll.promise);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(2);

  mocks.backend.createTestTask.mockResolvedValue(undefined);
  const postAction = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(postAction.promise);
  await act(async () => button("createTestTask").click());
  expect(mocks.message.success).not.toHaveBeenCalled();

  await act(async () => poll.resolve({ ...snapshot, updatedAt: "stale poll" }));
  expect(status.snapshot?.updatedAt).toBe("stale poll");
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(3);
  expect(mocks.message.success).not.toHaveBeenCalled();

  await act(async () => postAction.resolve({ ...snapshot, updatedAt: "after action" }));
  expect(status.snapshot?.updatedAt).toBe("after action");
  expect(mocks.message.success).toHaveBeenCalledExactlyOnceWith("测试任务已创建");
});

it.each(["resolve", "reject"])("starts a new account read immediately and ignores an old read's %s", async (outcome) => {
  await act(async () => root.render(<StatusHarness />));
  mocks.backend.getSystemStatus.mockRejectedValueOnce(new Error("old failure"));
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(status.lastFailure).toBeDefined();
  const old = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(old.promise);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  const next = deferred<SystemStatusSnapshot>();
  const nextSnapshot = { ...snapshot, context: { ...connectionSnapshot.account, epoch: "second" }, updatedAt: "second account" };
  mocks.backend.getSystemStatus.mockResolvedValue(nextSnapshot).mockReturnValueOnce(next.promise);
  await act(async () => selectAccount("second"));
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(4);
  expect(status.snapshot).toBeUndefined();
  expect(status.lastFailure).toBeUndefined();
  await act(async () => { if (outcome === "resolve") old.resolve(snapshot); else old.reject(new Error("late old failure")); });
  expect(status.snapshot).toBeUndefined();
  expect(status.lastFailure).toBeUndefined();
  expect(status.loading).toBe(true);
  expect(status.refreshing).toBe(true);
  await act(async () => next.resolve(nextSnapshot));
  expect(status.snapshot).toEqual(nextSnapshot);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(5);
});

it.each(["different", "missing"])("rejects a %s response epoch until the account provider reconciles identity", async (context) => {
  await act(async () => root.render(<StatusHarness />));
  const account = accountSession.get();
  const nextSnapshot = { ...snapshot, context: { ...connectionSnapshot.account, epoch: "second" }, updatedAt: "other workspace" };
  const observation = { ...nextSnapshot, context: context === "different" ? nextSnapshot.context : undefined };
  const pending = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockResolvedValue(observation).mockReturnValueOnce(pending.promise);
  let refreshed!: Promise<boolean>;
  await act(async () => { refreshed = status.refresh(); });
  await act(async () => pending.resolve(observation));
  expect(await refreshed).toBe(false);
  expect(status.snapshot).toEqual(snapshot);
  expect(status.lastFailure?.at).toBe(Date.now());
  expect(container.textContent).toContain("账号状态已变化");
  expect(status.loading).toBe(false);
  expect(status.refreshing).toBe(false);
  expect(accountSession.get()).toBe(account);
  await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(3);
  expect(status.snapshot).toEqual(snapshot);
  expect(status.lastFailure?.message).toContain("账号状态已变化");
  expect(accountSession.get()).toBe(account);
  mocks.backend.getSystemStatus.mockResolvedValue(nextSnapshot);
  await act(async () => selectAccount("second"));
  expect(status.snapshot).toEqual(nextSnapshot);
  expect(status.lastFailure).toBeUndefined();
});

it("allows observations before account identity is known without adopting the observed identity", async () => {
  accountSession.clear();
  const account = accountSession.get();
  const pending = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(pending.promise);
  await act(async () => root.render(<StatusHarness />));
  await act(async () => pending.resolve(snapshot));
  expect(status.snapshot).toEqual(snapshot);
  expect(status.lastFailure).toBeUndefined();
  expect(accountSession.get()).toBe(account);
});

it("pauses hidden reads and immediately resumes on visibility or focus without overlapping requests", async () => {
  setHidden(true);
  await act(async () => root.render(<StatusHarness />));
  await act(async () => {
    button("refresh").click();
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(6000);
  });
  expect(mocks.backend.getSystemStatus).not.toHaveBeenCalled();
  const pending = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(pending.promise);
  await act(async () => {
    setHidden(false);
    window.dispatchEvent(new Event("focus"));
  });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);
  await act(async () => { setHidden(true); pending.resolve(snapshot); });
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);
  await act(async () => setHidden(false));
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(2);
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(3);
  await act(async () => setHidden(true));
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(3);
});

it("keeps a failed initial read retryable and exposes manual refresh progress without clearing the snapshot", async () => {
  mocks.backend.getSystemStatus.mockRejectedValueOnce(new Error("initial failure"));
  await act(async () => root.render(<StatusHarness />));
  expect(status.snapshot).toBeUndefined();
  expect(status.loading).toBe(false);
  expect(status.lastFailure).toBeDefined();
  await act(async () => button("refresh").click());
  expect(status.snapshot).toEqual(snapshot);
  expect(status.lastFailure).toBeUndefined();
  const pending = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(pending.promise);
  await act(async () => { button("refresh").click(); button("refresh").click(); });
  expect(status.snapshot).toEqual(snapshot);
  expect(status.loading).toBe(false);
  expect(status.refreshing).toBe(true);
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(3);
  await act(async () => pending.reject(new Error("manual failure")));
  expect(status.snapshot).toEqual(snapshot);
  expect(status.refreshing).toBe(false);
  expect(status.lastFailure?.message).toContain("manual failure");
});

it.each(actions)("guards same-render double clicks for %s and releases the lock after failure", async (action, busy, notification) => {
  const pending = deferred<void>();
  mocks.backend[action].mockReturnValueOnce(pending.promise).mockResolvedValue(undefined);
  await act(async () => root.render(<StatusHarness />));
  await act(async () => {
    button(action).click();
    button(action === "runNodeTest" ? "testSearch" : action).click();
  });
  expect(mocks.backend[action]).toHaveBeenCalledTimes(1);
  expect(status[busy]).toBeTruthy();
  await act(async () => pending.reject(new ApiError("lost receipt", "/api/action", { kind: "transport", requestId: "action-lost" })));
  expect(status[busy]).toBeFalsy();
  expect(mocks.message.error).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("action-lost"));
  expect(mocks.backend[action]).toHaveBeenCalledTimes(1);
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);
  await act(async () => button(action).click());
  expect(mocks.backend[action]).toHaveBeenCalledTimes(2);
  expect(status[busy]).toBeFalsy();
  expect(mocks.message[notification]).toHaveBeenCalledTimes(1);
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(action === "runNodeTest" ? 1 : 2);
});

it.each(actions.flatMap(([action, busy]) => ["resolve", "reject"].map((outcome) => ({ action, busy, outcome }))))(
  "invalidates $action on account change before the old action settles ($outcome)",
  async ({ action, busy, outcome }) => {
    const old = deferred<void>();
    const next = deferred<void>();
    mocks.backend[action].mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    await act(async () => root.render(<StatusHarness />));
    await act(async () => button(action).click());
    mocks.backend.getSystemStatus.mockResolvedValue({ ...snapshot, context: { ...connectionSnapshot.account, epoch: "second" } });
    await act(async () => selectAccount("second"));
    expect(status[busy]).toBeFalsy();
    await act(async () => button(action).click());
    expect(status[busy]).toBeTruthy();
    await act(async () => { if (outcome === "resolve") old.resolve(); else old.reject(new Error("old failure")); });
    expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(2);
    for (const notify of Object.values(mocks.message)) expect(notify).not.toHaveBeenCalled();
    expect(status[busy]).toBeTruthy();
    await act(async () => button(action).click());
    expect(mocks.backend[action]).toHaveBeenCalledTimes(2);
    await act(async () => next.resolve());
    expect(status[busy]).toBeFalsy();
  },
);

it.each(["resolve", "reject"])("suppresses action continuations, reads and events after unmount (%s)", async (outcome) => {
  const pendingActions = actions.map(([action]) => {
    const pending = deferred<void>();
    mocks.backend[action].mockReturnValueOnce(pending.promise);
    return pending;
  });
  const read = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(read.promise);
  await act(async () => root.render(<StatusHarness />));
  await act(async () => { for (const [action] of actions) button(action).click(); });
  await act(async () => root.render(null));
  await act(async () => {
    for (const pending of pendingActions) {
      if (outcome === "resolve") pending.resolve(); else pending.reject(new Error("late failure"));
    }
    if (outcome === "resolve") read.resolve(snapshot); else read.reject(new Error("late read failure"));
  });
  await act(async () => {
    selectAccount("second");
    setHidden(false);
    window.dispatchEvent(new Event("focus"));
    await status.refresh();
    await status.createTestTask();
    await status.runNodeTest("ChatInput_GoToInput");
    await status.sweepCards();
    await vi.advanceTimersByTimeAsync(6000);
  });
  for (const notify of Object.values(mocks.message)) expect(notify).not.toHaveBeenCalled();
  for (const [action] of actions) expect(mocks.backend[action]).toHaveBeenCalledTimes(1);
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);
});

it.each(["createTestTask", "sweepCards"] as const)("checks account scope again after %s's follow-up read", async (action) => {
  mocks.backend[action].mockResolvedValue(undefined);
  await act(async () => root.render(<StatusHarness />));
  const pending = deferred<SystemStatusSnapshot>();
  mocks.backend.getSystemStatus.mockReturnValueOnce(pending.promise);
  await act(async () => button(action).click());
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(2);
  const nextSnapshot = { ...snapshot, context: { ...connectionSnapshot.account, epoch: "second" } };
  mocks.backend.getSystemStatus.mockResolvedValue(nextSnapshot);
  await act(async () => selectAccount("second"));
  await act(async () => pending.resolve({ ...snapshot, updatedAt: "old action refresh" }));
  expect(status.snapshot).toEqual(nextSnapshot);
  for (const notify of Object.values(mocks.message)) expect(notify).not.toHaveBeenCalled();
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(3);
});

it.each(["createTestTask", "sweepCards"] as const)("reports accepted %s with a failed status read as unsynchronized", async (action) => {
  mocks.backend[action].mockResolvedValue(undefined);
  await act(async () => root.render(<StatusHarness />));
  mocks.backend.getSystemStatus.mockRejectedValueOnce(new Error("status failed"));
  await act(async () => button(action).click());
  expect(status.snapshot).toEqual(snapshot);
  expect(status.lastFailure).toBeDefined();
  expect(mocks.message.warning).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("状态未同步"));
  expect(mocks.message.error).not.toHaveBeenCalled();
  expect(mocks.backend[action]).toHaveBeenCalledTimes(1);
});

it.each(["accepted", "rejected", "lost"])("reports shutdown %s without claiming process exit or closing the tab", async (outcome) => {
  const close = vi.spyOn(window, "close").mockImplementation(() => {});
  if (outcome === "lost") mocks.backend.shutdownApp.mockRejectedValue(new ApiError("connection lost", "/api/app/shutdown", { kind: "transport", requestId: "stop-1" }));
  else mocks.backend.shutdownApp.mockResolvedValue({ accepted: outcome === "accepted" });
  await act(async () => root.render(<ShutdownHarness />));
  await act(async () => shutdown.confirmShutdown());
  expect(shutdown.terminated).toBe(outcome === "accepted");
  if (outcome === "rejected") expect(container.textContent).toContain("未接受");
  if (outcome === "lost") expect(container.textContent).toContain("结果未知");
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(close).not.toHaveBeenCalled();
  expect(mocks.backend.shutdownApp).toHaveBeenCalledTimes(1);
});
