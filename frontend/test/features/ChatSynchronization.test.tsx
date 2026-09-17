// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { useChatWorkbench } from "@/features/chat/hooks/useChatWorkbench";
import { accountSession } from "@/services/accountSession";
import { adaptConversationDetail, adaptConversationSummary } from "@/services/chatAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import type { ConnectionSnapshot } from "@/types/connection";
import type { ConversationAggregateDto } from "@/types/chatTransport";
import { draftKey } from "@/features/account/draftStorage";

const mocks = vi.hoisted(() => ({
  message: { warning: vi.fn(), error: vi.fn() },
  backend: { getConnection: vi.fn(), getConversationRevision: vi.fn(), listConversations: vi.fn(), getConversation: vi.fn(), retryConnection: vi.fn() },
}));
vi.mock("antd", () => ({ App: { useApp: () => ({ message: mocks.message }) } }));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));

const aggregate: ConversationAggregateDto = {
  sid: 42, name: "Buyer", participants: [], messages: [],
  latest: { content: "latest", updated_at: "2026-09-08 10:00" },
  unread_count: 0, status: "following", priority: "medium",
};
let root: Root;
let container: HTMLDivElement;
let workbench: ReturnType<typeof useChatWorkbench>;
let account: ReturnType<typeof useAccount>;

function Workspace() {
  const current = useChatWorkbench();
  useEffect(() => { workbench = current; }, [current]);
  return <textarea value={current.draft} readOnly />;
}

function ReadableWorkspace() {
  const current = useAccount();
  useEffect(() => { account = current; }, [current]);
  if (current.blocked || !current.snapshot?.capabilities.read_chat) return null;
  return <Workspace key={`${current.snapshot.account.epoch}:${current.generation}`} />;
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  mocks.backend.getConnection.mockReset().mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.listConversations.mockReset().mockResolvedValue([adaptConversationSummary(aggregate)]);
  mocks.backend.getConversation.mockReset().mockResolvedValue(adaptConversationDetail(aggregate));
  mocks.backend.getConversationRevision.mockReset().mockResolvedValue({ ready: true, revision: connectionSnapshot.source.revision });
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
  it("polls a validated source every ten visible seconds and refreshes new messages", async () => {
    vi.useFakeTimers();
    await mount();
    const before = container.querySelector("textarea");
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    const updated = adaptConversationDetail(aggregate);
    updated.messages = [{ id: "new-message", role: "buyer", content: "new buyer message", createdAt: "2026-09-17 12:00", type: "text" }];
    mocks.backend.getConversation.mockResolvedValue(updated);
    mocks.backend.getConversationRevision.mockResolvedValueOnce({ ready: true, revision: 2 });
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

  it.each(["unverified", "invalid", "unavailable"] as const)("does not automatically synchronize a %s key, even with an archive", async (keyValidation) => {
    vi.useFakeTimers();
    const snapshot = { ...connectionSnapshot, source: { ...connectionSnapshot.source, key_validation: keyValidation } };
    mocks.backend.getConnection.mockResolvedValue(snapshot);
    await mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(workbench.activeConversation?.id).toBe("42");
    expect(mocks.backend.getConversationRevision).not.toHaveBeenCalled();
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
    mocks.backend.getConnection.mockResolvedValue(connectionSnapshot);
    await observe(connectionSnapshot);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(1);
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
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.getConversationRevision).toHaveBeenCalledTimes(2);
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
    await act(async () => resolve({ ready: true, revision: 99 }));
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

    // Advance the actual Provider interval; its failed GET unmounts the workspace.
    vi.useFakeTimers();
    // Remount the provider so its interval is registered with the fake clock.
    await act(async () => root.render(<AccountProvider key="poll-test"><ReadableWorkspace /></AccountProvider>));
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("poll offline"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(account.blocked).toBe(true);
    expect(container.querySelector("textarea")).toBeNull();
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

  it("keeps an available archive mounted and reloads after sync completes without another numeric revision", async () => {
    await mount();
    await act(async () => workbench.setDraft("archive draft"));
    const before = container.querySelector("textarea");
    await observe({
      ...connectionSnapshot,
      source: { ...connectionSnapshot.source, phase: "syncing", ready: false, stale: true, revision: 2 },
    });
    expect(container.querySelector("textarea")).toBe(before);
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    await observe({ ...connectionSnapshot, source: { ...connectionSnapshot.source, revision: 2, last_success: 1_789_600_200 } });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    expect(workbench.draft).toBe("archive draft");
    expect(container.querySelector("textarea")).toBe(before);
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
