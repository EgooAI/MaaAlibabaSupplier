// @vitest-environment happy-dom
import { act, useEffect, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { SyncStatus } from "@/features/account/SyncStatus";
import { useChatWorkbench } from "@/features/chat/hooks/useChatWorkbench";
import { useBatchManagement } from "@/features/chat/hooks/useBatchManagement";
import { accountSession } from "@/services/accountSession";
import { adaptConversationDetail, adaptConversationSummary } from "@/services/chatAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import type { ConnectionSnapshot } from "@/types/connection";
import type { ConversationAggregateDto } from "@/types/chatTransport";
import type { ConversationDetail } from "@/types/chatCanonical";
import { draftKey } from "@/features/account/draftStorage";
import type { ConversationPage } from "@/types/inbox";
import { authenticatedSession } from "@/test/support/authFixture";

const pageOf = (items = [adaptConversationSummary(aggregate)]): ConversationPage => ({ items, total: items.length, offset: 0, limit: 50, inbox_revision: 1, pagination_revision: "page-1" });
const revisionOf = (revision: number) => ({ ready: true, revision, inbox_revision: 1, next_due_at: null });

const mocks = vi.hoisted(() => ({
  message: { warning: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn() },
  backend: { getConnection: vi.fn(), getConversationRevision: vi.fn(), listConversations: vi.fn(), getConversation: vi.fn(), retryConnection: vi.fn(), requestTranslations: vi.fn(), queryTranslations: vi.fn(), getTranslationJob: vi.fn(), analyzeConversation: vi.fn() },
}));
vi.mock("antd", () => ({
  App: { useApp: () => ({ message: mocks.message }) },
  Space: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  Tag: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  Typography: { Text: ({ children }: { children: ReactNode }) => <span>{children}</span> },
  Button: ({ children, disabled, loading, onClick }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
  Descriptions: ({ items }: { items: Array<{ key: string; label: string; children: ReactNode }> }) => <dl>{items.map((item) => <div key={item.key}><dt>{item.label}</dt><dd>{item.children}</dd></div>)}</dl>,
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));

const aggregate: ConversationAggregateDto = {
  sid: 42, name: "Buyer", participants: [], messages: [],
  latest: { content: "latest", updated_at: "2026-09-08 10:00" },
  unread_count: 0, reply_state: "waiting_customer", pending_since: null, due_at: null, is_overdue: false, history_pending: false, uncertain: false, read_snapshot: "snapshot-42",
};
let root: Root;
let container: HTMLDivElement;
let workbench: ReturnType<typeof useChatWorkbench>;
let account: ReturnType<typeof useAccount>;
let batch: ReturnType<typeof useBatchManagement>;

function BatchWorkspace() {
  const current = useBatchManagement();
  useEffect(() => { batch = current; }, [current]);
  return <><input value={current.selectedIds.join(",")} readOnly /><SyncStatus refreshError={current.refreshError} refreshPending={current.refreshPending} /></>;
}

function Workspace() {
  const current = useChatWorkbench();
  useEffect(() => { workbench = current; }, [current]);
  return <><textarea value={current.draft} readOnly /><SyncStatus refreshError={current.refreshError} refreshPending={current.refreshPending} /></>;
}

function ReadableWorkspace({ isBatch = false }: { isBatch?: boolean }) {
  const current = useAccount();
  useEffect(() => { account = current; }, [current]);
  if ((current.blocked && !current.suspended) || !current.snapshot?.capabilities.read_chat) return null;
  const Component = isBatch ? BatchWorkspace : Workspace;
  return <Component key={`${current.snapshot.account.epoch}:${current.generation}`} />;
}

beforeEach(async () => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  mocks.backend.getConnection.mockReset().mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.listConversations.mockReset().mockResolvedValue(pageOf());
  mocks.backend.getConversation.mockReset().mockResolvedValue(adaptConversationDetail(aggregate));
  mocks.backend.getConversationRevision.mockReset().mockResolvedValue(revisionOf(connectionSnapshot.source.revision));
  mocks.backend.retryConnection.mockReset().mockResolvedValue(connectionSnapshot);
  mocks.backend.requestTranslations.mockReset().mockResolvedValue({ task_id: "", status: "succeeded", message: "没有需要翻译的内容" });
  mocks.backend.queryTranslations.mockReset().mockResolvedValue({ translations: {} });
  mocks.backend.getTranslationJob.mockReset().mockResolvedValue(null);
  mocks.backend.analyzeConversation.mockReset();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

async function mount() {
  await act(async () => root.render(<AccountProvider><ReadableWorkspace /></AccountProvider>));
}

async function observe(snapshot: ConnectionSnapshot) {
  mocks.backend.getConnection.mockResolvedValueOnce(snapshot);
  await act(async () => { await account.refresh(); });
}

describe("chat synchronization boundaries", () => {
  it("shows automatic sync progress and permits manual refresh after completion without replacing the workspace", async () => {
    const source = { ...connectionSnapshot.source, syncing: true, pending: true, freshness: "syncing" as const, stale: true, last_error: "source offline", retry_at: 1_789_600_300 };
    mocks.backend.getConnection.mockResolvedValue({ ...connectionSnapshot, source });
    await act(async () => root.render(<AccountProvider><ReadableWorkspace /><SyncStatus /></AccountProvider>));
    expect(container.textContent).toContain("同步中");
    expect(container.textContent).toContain("正在重试同步");
    expect(container.textContent).toContain("存档可能过期");
    const before = container.querySelector("textarea");
    const generation = account.generation;
    expect(container.querySelector("button")!.disabled).toBe(true);
    await act(async () => container.querySelector("button")!.click());
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
    await observe(connectionSnapshot);
    await act(async () => container.querySelector("button")!.click());
    expect(mocks.backend.retryConnection).toHaveBeenCalledExactlyOnceWith(connectionSnapshot.account.epoch);
    expect(mocks.message.info).toHaveBeenCalledWith("同步请求已提交，完成情况请查看同步状态");
    expect(account.generation).toBe(generation);
    expect(container.querySelector("textarea")).toBe(before);
    expect(localStorage.getItem("maa:account-changed")).toBeNull();
  });

  it("polls a validated source every ten visible seconds and refreshes new messages", async () => {
    vi.useFakeTimers();
    await mount();
    const before = container.querySelector("textarea");
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    const updated = adaptConversationDetail(aggregate);
    updated.messages = [{ id: "new-message", role: "buyer", content: "new buyer message", createdAt: "2026-09-17 12:00", type: "text" }];
    mocks.backend.getConversation.mockResolvedValue(updated);
    mocks.backend.getConversationRevision.mockResolvedValueOnce(revisionOf(2));
    await act(async () => { await vi.advanceTimersByTimeAsync(9_999); });
    expect(mocks.backend.getConversationRevision).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(1);
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    expect(workbench.activeConversation?.messages[0].content).toBe("new buyer message");
    expect(container.querySelector("textarea")).toBe(before);
    Object.defineProperty(document, "hidden", { value: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "hidden", { value: false });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(2);
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
  });

  it.each(["unverified", "verifying", "invalid", "unavailable"] as const)("reads archive revisions with a %s key without requesting sync", async (keyValidation) => {
    vi.useFakeTimers();
    const snapshot = { ...connectionSnapshot, source: { ...connectionSnapshot.source, key_validation: keyValidation } };
    mocks.backend.getConnection.mockResolvedValue(snapshot);
    await mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(workbench.activeConversation?.id).toBe("42");
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(4);
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
    mocks.backend.getConnection.mockResolvedValue(connectionSnapshot);
    await observe(connectionSnapshot);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(5);
  });

  it("coalesces blocked revision polls and preserves the workspace on failure", async () => {
    vi.useFakeTimers();
    await mount();
    await act(async () => workbench.setDraft("keep this draft"));
    const before = container.querySelector("textarea");
    const conversations = workbench.conversations;
    const detail = workbench.activeConversation;
    let reject!: (error: Error) => void;
    mocks.backend.getConversationRevision.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(1);
    expect(mocks.backend.getConnection.mock.calls.length).toBeGreaterThan(1);
    await act(async () => reject(new Error("account lock timeout")));
    expect(account.blocked).toBe(false);
    expect(container.querySelector("textarea")).toBe(before);
    expect(workbench.conversations).toBe(conversations);
    expect(workbench.activeConversation).toBe(detail);
    expect(workbench.draft).toBe("keep this draft");
    expect(mocks.message.error).not.toHaveBeenCalled();
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(2);
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
  });

  it("discards a blocked old-account revision after switching workspaces", async () => {
    vi.useFakeTimers();
    await mount();
    let resolve!: (value: { ready: boolean; revision: number }) => void;
    mocks.backend.getConversationRevision.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    const next = { ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "b", self_ali_id: "seller-b" } };
    mocks.backend.getConnection.mockResolvedValue(next);
    await observe(next);
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    await act(async () => resolve(revisionOf(99)));
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
    expect(account.snapshot?.account.self_ali_id).toBe("seller-b");
    expect(mocks.message.error).not.toHaveBeenCalled();
  });

  it("retains quota-failed drafts through a failed background poll, account switches and stale storage notifications", async () => {
    await mount();
    await act(async () => workbench.setDraft("older persisted draft"));
    const storage = localStorage;
    const key = draftKey(connectionSnapshot.account, "42");
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.getItem(key),
      setItem: () => { throw new DOMException("quota exceeded", "QuotaExceededError"); },
      removeItem: (key: string) => storage.removeItem(key),
    });
    await act(async () => workbench.setDraft("new unsaved draft"));
    expect(mocks.message.warning).toHaveBeenCalled();
    expect(storage.getItem(key)).toBe("older persisted draft");

    // Advance the actual Provider interval; a failed GET suspends writes only.
    vi.useFakeTimers();
    // Remount the provider so its interval is registered with the fake clock.
    await act(async () => root.render(<AccountProvider key="poll-test"><ReadableWorkspace /></AccountProvider>));
    const before = container.querySelector("textarea");
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("poll offline"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(account.blocked).toBe(true);
    expect(container.querySelector("textarea")).toBe(before);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(workbench.draft).toBe("new unsaved draft");

    await observe({ ...connectionSnapshot, account: { ...connectionSnapshot.account, self_ali_id: "seller-b", epoch: "b" } });
    expect(workbench.draft).toBe("");
    await observe({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "a-returned" } });
    expect(workbench.draft).toBe("new unsaved draft");
    await act(async () => window.dispatchEvent(new StorageEvent("storage", { key, newValue: "older persisted draft" })));
    expect(workbench.draft).toBe("new unsaved draft");
    await act(async () => window.dispatchEvent(new StorageEvent("storage", { key: "maa:account-changed", newValue: "external-change" })));
    expect(workbench.draft).toBe("new unsaved draft");

    vi.unstubAllGlobals();
    await act(async () => { await account.refresh(); });
    expect(storage.getItem(key)).toBe("new unsaved draft");
    expect(workbench.draft).toBe("new unsaved draft");
  });

  it("restores the edited draft after syncing briefly removes read capability", async () => {
    await mount();
    expect(workbench.activeConversation?.id).toBe("42");
    await act(async () => workbench.setDraft("unsent seller reply"));
    const before = container.querySelector("textarea");
    await observe({
      ...connectionSnapshot,
      source: { ...connectionSnapshot.source, phase: "syncing", ready: false },
      capabilities: { ...connectionSnapshot.capabilities, read_chat: false },
    });
    expect(container.querySelector("textarea")).toBeNull();
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, last_success: 1_789_600_100 } });
    expect(container.querySelector("textarea")).not.toBe(before);
    expect(workbench.draft).toBe("unsent seller reply");
    expect(container.querySelector("textarea")?.value).toBe("unsent seller reply");
  });

  it("ignores source progress and success timestamps, reloading only a committed revision", async () => {
    vi.useFakeTimers();
    await mount();
    await act(async () => workbench.setDraft("archive draft"));
    const before = container.querySelector("textarea");
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    await observe({
      ...connectionSnapshot,
      source: { ...connectionSnapshot.source, phase: "syncing", ready: false, stale: true, source_revision: 2, syncing: true, pending: true, freshness: "syncing" },
    });
    expect(container.querySelector("textarea")).toBe(before);
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, last_success: 1_789_600_200 } });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
    mocks.backend.getConversationRevision.mockResolvedValue({ ...connectionSnapshot.source, ...revisionOf(connectionSnapshot.source.revision), source_revision: 3, syncing: true, pending: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, revision: 2, source_revision: 2, applied_source_revision: 2 } });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    expect(workbench.draft).toBe("archive draft");
    expect(container.querySelector("textarea")).toBe(before);
  });

  it.each(["list", "detail"])("retries a failed %s read once at the same committed revision without a loading skeleton", async (target) => {
    vi.useFakeTimers();
    await mount();
    const before = container.querySelector("textarea");
    const failing = target === "list" ? mocks.backend.listConversations : mocks.backend.getConversation;
    failing.mockRejectedValueOnce(new Error("offline"));
    mocks.backend.getConversationRevision.mockResolvedValue(revisionOf(2));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(workbench.refreshError).toBe(true);
    expect(workbench.loading).toBe(false);
    expect(workbench.detailLoading).toBe(false);
    const calls = failing.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(failing).toHaveBeenCalledTimes(calls + 1);
    expect(workbench.refreshError).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(failing).toHaveBeenCalledTimes(calls + 1);
    expect(container.querySelector("textarea")).toBe(before);
  });

  it.each(["list", "detail"])("recovers an initial %s failure at the unchanged revision", async (target) => {
    vi.useFakeTimers();
    const failing = target === "list" ? mocks.backend.listConversations : mocks.backend.getConversation;
    failing.mockRejectedValueOnce(new Error("initial read failed"));
    await mount();
    expect(workbench.activeConversation).toBeUndefined();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(workbench.activeConversation?.id).toBe("42");
    expect(workbench.detailLoading).toBe(false);
    const calls = failing.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(failing).toHaveBeenCalledTimes(calls);
  });

  it.each(["list", "detail"])("keeps the recovery warning until both reads succeed, with %s finishing first", async (first) => {
    vi.useFakeTimers();
    await mount();
    mocks.backend.getConversationRevision.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(workbench.refreshError).toBe(true);
    let resolveList!: (value: ConversationPage) => void;
    let resolveDetail!: (value: ConversationDetail) => void;
    mocks.backend.listConversations.mockReturnValueOnce(new Promise((done) => { resolveList = done; }));
    mocks.backend.getConversation.mockReturnValueOnce(new Promise((done) => { resolveDetail = done; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    const finish = (kind: string) => kind === "list" ? resolveList(pageOf()) : resolveDetail(adaptConversationDetail(aggregate));
    await act(async () => finish(first));
    expect(workbench.refreshPending).toBe(true);
    expect(workbench.refreshError).toBe(true);
    expect(container.textContent).toContain("聊天读取失败，正在重试");
    expect(container.textContent).not.toContain("聊天已更新");
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
    expect(workbench.refreshError).toBe(true);
    expect(workbench.refreshPending).toBe(true);
    await act(async () => finish(first === "list" ? "detail" : "list"));
    expect(workbench.refreshError).toBe(false);
    expect(workbench.refreshPending).toBe(false);
    expect(container.textContent).toContain("聊天已更新");
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
  });

  it("retains a recovery error when a deferred detail fails after the list succeeds", async () => {
    vi.useFakeTimers();
    await mount();
    mocks.backend.getConversationRevision.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    let reject!: (error: Error) => void;
    mocks.backend.getConversation.mockReturnValueOnce(new Promise((_, fail) => { reject = fail; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(workbench.refreshError).toBe(true);
    await act(async () => reject(new Error("detail still offline")));
    expect(workbench.refreshPending).toBe(false);
    expect(workbench.refreshError).toBe(true);
    expect(container.textContent).toContain("聊天读取失败，等待重试");
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(workbench.refreshError).toBe(false);
    expect(workbench.refreshPending).toBe(false);
  });

  it("acknowledges recovery after the list succeeds when no detail is required", async () => {
    vi.useFakeTimers();
    await act(async () => root.render(<AccountProvider><ReadableWorkspace isBatch /></AccountProvider>));
    mocks.backend.getConversationRevision.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    let resolve!: (value: ConversationPage) => void;
    mocks.backend.listConversations.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    const calls = mocks.backend.listConversations.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(calls);
    expect(batch.refreshError).toBe(true);
    expect(batch.refreshPending).toBe(true);
    await act(async () => resolve(pageOf()));
    expect(batch.refreshError).toBe(false);
    expect(batch.refreshPending).toBe(false);
    expect(mocks.backend.getConversation).not.toHaveBeenCalled();
  });

  it("manually refreshes changed CRM profiles at the same source and committed revision after submission", async () => {
    vi.useFakeTimers();
    await mount();
    await act(async () => workbench.setDraft("keep selected draft"));
    const before = container.querySelector("textarea")!;
    before.scrollTop = 80;
    const generation = account.generation;
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    const updated = adaptConversationDetail(aggregate);
    updated.customer = { ...updated.customer, name: "MITM updated buyer", company: "New company", email: "buyer@example.test" };
    mocks.backend.listConversations.mockResolvedValue(pageOf([updated]));
    mocks.backend.getConversation.mockResolvedValue(updated);
    let finishDetail!: (value: ConversationDetail) => void;
    mocks.backend.getConversation.mockReturnValueOnce(new Promise((done) => { finishDetail = done; }));
    let submitted!: (value: ConnectionSnapshot) => void;
    mocks.backend.retryConnection.mockReturnValueOnce(new Promise((done) => { submitted = done; }));
    await act(async () => container.querySelector("button")!.click());
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
    await act(async () => submitted(connectionSnapshot));
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    expect(workbench.refreshPending).toBe(true);
    expect(container.textContent).toContain("正在刷新聊天");
    expect(container.textContent).not.toContain("聊天已更新");
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(workbench.refreshPending).toBe(true);
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    await act(async () => finishDetail(updated));
    expect(workbench.refreshPending).toBe(false);
    expect(workbench.conversations[0].customer).toEqual(updated.customer);
    expect(workbench.activeConversation?.customer).toEqual(updated.customer);
    expect(workbench.activeConversation?.id).toBe("42");
    expect(workbench.draft).toBe("keep selected draft");
    expect(account.generation).toBe(generation);
    expect(account.snapshot?.source.revision).toBe(connectionSnapshot.source.revision);
    expect(container.querySelector("textarea")).toBe(before);
    expect(before.scrollTop).toBe(80);
    expect(mocks.backend.requestTranslations).not.toHaveBeenCalled();
    expect(mocks.backend.analyzeConversation).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
  });

  it("reloads once after connection recovery at the same revision and retains selection, scroll and draft", async () => {
    vi.useFakeTimers();
    await mount();
    await act(async () => workbench.setDraft("draft during outage"));
    const before = container.querySelector("textarea")!;
    before.scrollTop = 60;
    const generation = account.generation;
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await account.refresh(); });
    expect(account.suspended).toBe(true);
    expect(account.generation).toBe(generation);
    expect(container.querySelector("textarea")).toBe(before);
    const calls = mocks.backend.listConversations.mock.calls.length;
    await observe(connectionSnapshot);
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(calls + 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(calls + 1);
    expect(workbench.activeConversation?.id).toBe("42");
    expect(workbench.draft).toBe("draft during outage");
    expect(container.querySelector("textarea")).toBe(before);
    expect(before.scrollTop).toBe(60);
  });

  it("keeps batch selection through manual sync, source progress and a transient outage", async () => {
    await act(async () => root.render(<AccountProvider><ReadableWorkspace isBatch /></AccountProvider>));
    await act(async () => batch.toggleSelected("42"));
    const before = container.querySelector("input");
    const generation = account.generation;
    const calls = mocks.backend.listConversations.mock.calls.length;
    await act(async () => { await account.requestSync(); });
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, syncing: true, source_revision: 2 } });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(calls + 1);
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await account.refresh(); });
    await observe(connectionSnapshot);
    expect(batch.selectedIds).toEqual(["42"]);
    expect(account.generation).toBe(generation);
    expect(container.querySelector("input")).toBe(before);
    expect(mocks.backend.retryConnection).toHaveBeenCalledExactlyOnceWith(connectionSnapshot.account.epoch);
  });

  it("merges job translations by original text and preserves AI results completed during a quiet read", async () => {
    vi.useFakeTimers();
    const original = adaptConversationDetail(aggregate);
    original.messages = [
      { id: "one", role: "buyer", content: "hello", createdAt: "now", translatedContent: "old translation" },
      { id: "two", role: "buyer", content: "original", createdAt: "now", translatedContent: "must be dropped" },
    ];
    mocks.backend.getConversation.mockResolvedValue(original);
    await mount();
    const before = container.querySelector("textarea");
    let resolve!: (value: ConversationDetail) => void;
    mocks.backend.getConversation.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    mocks.backend.getConversationRevision.mockResolvedValue(revisionOf(2));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(workbench.detailLoading).toBe(false);
    expect(mocks.backend.requestTranslations).not.toHaveBeenCalled();
    expect(mocks.backend.analyzeConversation).not.toHaveBeenCalled();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-1", status: "pending", message: "等待执行" });
    mocks.backend.getTranslationJob.mockResolvedValueOnce({ task_id: "job-1", status: "succeeded", message: "已翻译 1 条" });
    mocks.backend.queryTranslations.mockResolvedValueOnce({ translations: { hello: "just translated" } });
    const analysis = { intent: "intent", stage: "new", score: 80, risks: [], nextActions: [], summary: "just analyzed" };
    mocks.backend.analyzeConversation.mockResolvedValue(analysis);
    await act(async () => { await workbench.translateMessages([original.messages[0]], { force: true }); await workbench.analyzeConversation(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(workbench.activeConversation?.messages[0].translatedContent).toBe("just translated");
    const incoming = structuredClone(original);
    incoming.messages[0].translatedContent = "stale server translation";
    incoming.messages[1] = { ...incoming.messages[1], content: "changed", translatedContent: undefined };
    incoming.messages.push({ id: "three", role: "buyer", content: "new", createdAt: "now" });
    await act(async () => resolve(incoming));
    expect(workbench.activeConversation?.messages.map((item) => item.translatedContent)).toEqual(["just translated", undefined, undefined]);
    expect(workbench.activeConversation?.analysis).toEqual(analysis);
    expect(container.querySelector("textarea")).toBe(before);
    expect(mocks.backend.requestTranslations).toHaveBeenCalledTimes(1);
    expect(mocks.backend.analyzeConversation).toHaveBeenCalledTimes(1);
  });

  it("discards old detail and list responses when a newer committed revision overtakes them", async () => {
    await mount();
    let oldDetail!: (value: ConversationDetail) => void;
    let oldList!: (value: ConversationPage) => void;
    mocks.backend.getConversation.mockReturnValueOnce(new Promise((done) => { oldDetail = done; }));
    mocks.backend.listConversations.mockReturnValueOnce(new Promise((done) => { oldList = done; }));
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, revision: 2 } });
    const latest = adaptConversationDetail({ ...aggregate, name: "latest commit" });
    mocks.backend.getConversation.mockResolvedValue(latest);
    mocks.backend.listConversations.mockResolvedValue(pageOf([latest]));
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, revision: 3 } });
    await act(async () => { oldDetail(adaptConversationDetail(aggregate)); oldList(pageOf([])); });
    expect(workbench.activeConversation).toEqual(latest);
    expect(workbench.conversations).toEqual([latest]);
  });

  it("does not apply a late translation when a quiet refresh changed the original text", async () => {
    vi.useFakeTimers();
    const original = adaptConversationDetail(aggregate);
    original.messages = [{ id: "one", role: "buyer", content: "before", createdAt: "now" }];
    mocks.backend.getConversation.mockResolvedValue(original);
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-late", status: "pending", message: "等待执行" });
    mocks.backend.getTranslationJob.mockResolvedValueOnce({ task_id: "job-late", status: "succeeded", message: "已翻译 1 条" });
    // Cache holds a translation keyed by the OLD text; the message text changed meanwhile.
    mocks.backend.queryTranslations.mockResolvedValue({ translations: { before: "translation of before" } });
    await act(async () => { await workbench.translateMessages([original.messages[0]]); });
    mocks.backend.getConversation.mockResolvedValue({ ...original, messages: [{ ...original.messages[0], content: "after" }] });
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, revision: 2 } });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(workbench.activeConversation?.messages[0]).toMatchObject({ content: "after" });
    expect(workbench.activeConversation?.messages[0].translatedContent).toBeUndefined();
  });

  it("rejects late AI results after switching away and returning to the same conversation", async () => {
    const original = adaptConversationDetail(aggregate);
    original.messages = [{ id: "one", role: "buyer", content: "hello", createdAt: "now" }];
    mocks.backend.getConversation.mockImplementation(async (id: string) => ({ ...original, id }));
    await mount();
    let resolveSubmission!: (value: { task_id: string; status: string; message: string }) => void;
    let resolveAnalysis!: (value: ConversationDetail["analysis"]) => void;
    mocks.backend.requestTranslations.mockReturnValueOnce(new Promise((done) => { resolveSubmission = done; }));
    mocks.backend.analyzeConversation.mockReturnValueOnce(new Promise((done) => { resolveAnalysis = done; }));
    let translating!: Promise<void>;
    let analyzing!: Promise<void>;
    await act(async () => { translating = workbench.translateMessages([original.messages[0]]); analyzing = workbench.analyzeConversation(); });
    await act(async () => { await workbench.selectConversation("other"); });
    await act(async () => { await workbench.selectConversation("42"); });
    const current = workbench.activeConversation;
    await act(async () => {
      resolveSubmission({ task_id: "job-obsolete", status: "pending", message: "" });
      resolveAnalysis({ intent: "old", stage: "new", score: 20, risks: [], nextActions: [], summary: "obsolete analysis" });
      await translating;
      await analyzing;
    });
    expect(workbench.activeConversation).toBe(current);
    expect(workbench.activeConversation?.messages[0].translatedContent).toBeUndefined();
    expect(workbench.activeConversation?.analysis).toBeUndefined();
    expect(mocks.message.success).not.toHaveBeenCalled();
  });

  it("keeps the selected seller archive readable on source error without crossing account drafts", async () => {
    await mount();
    await act(async () => workbench.setDraft("seller A draft"));
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, phase: "error", ready: false, stale: true, last_error: "source unavailable" } });
    expect(workbench.draft).toBe("seller A draft");
    await observe({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "seller-b-epoch", self_ali_id: "seller-b" } });
    expect(workbench.draft).toBe("");
    await observe({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "seller-a-returned" } });
    expect(workbench.draft).toBe("seller A draft");
  });
});
