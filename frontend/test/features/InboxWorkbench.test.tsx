// @vitest-environment happy-dom
import { act, useEffect, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { useChatWorkbench } from "@/features/chat/hooks/useChatWorkbench";
import { useBatchManagement } from "@/features/chat/hooks/useBatchManagement";
import { InboxBadges } from "@/features/chat/conversation/InboxBadges";
import { ConversationPaging } from "@/features/chat/conversation/ConversationFilters";
import { accountSession } from "@/services/accountSession";
import { ApiError } from "@/services/httpAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import { adaptConversationDetail } from "@/services/chatAdapter";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { ConversationPage, ReadReceipt } from "@/types/inbox";
import { authenticatedSession } from "@/test/support/authFixture";

const mocks = vi.hoisted(() => ({
  message: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
  backend: { getConnection: vi.fn(), getConversationRevision: vi.fn(), listConversations: vi.fn(), getConversation: vi.fn(), markConversationRead: vi.fn(), requestTranslations: vi.fn(), queryTranslations: vi.fn(), getTranslationJob: vi.fn(), exportConversations: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => ({
  App: { useApp: () => ({ message: mocks.message }) },
  Space: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Tag: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  Typography: { Text: ({ children }: { children: ReactNode }) => <span>{children}</span> },
  Button: ({ children, disabled, onClick }: ButtonHTMLAttributes<HTMLButtonElement>) => <button disabled={disabled} onClick={onClick}>{children}</button>,
}));

const detail = (id = "42", changes: Partial<ConversationDetail> = {}): ConversationDetail => ({
  ...adaptConversationDetail({
    sid: Number(id), name: "buyer", participants: [], messages: [], latest: { content: "hello", updated_at: "2026-09-17 10:00" },
    unread_count: 3, reply_state: "needs_reply", pending_since: 100, due_at: 86500, is_overdue: true, history_pending: false, uncertain: false, read_snapshot: "displayed-token",
  }),
  ...changes,
});
const page = (items = [detail()], offset = 0, total = items.length, token = "page-1"): ConversationPage => ({ items, total, offset, limit: 50, inbox_revision: 1, pagination_revision: token });
const revision = { ...connectionSnapshot.source, ready: true, inbox_revision: 1, next_due_at: null };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

let root: Root;
let container: HTMLDivElement;
let chat: ReturnType<typeof useChatWorkbench>;
let batch: ReturnType<typeof useBatchManagement>;
let account: ReturnType<typeof useAccount>;

function Chat() {
  const current = useChatWorkbench();
  useEffect(() => { chat = current; }, [current]);
  return <>
    <textarea value={current.draft} readOnly />
    <output>{current.conversations.map((item) => item.id).join(",")}</output>
    {current.activeConversation && <InboxBadges conversation={current.activeConversation} />}
    <button onClick={() => void current.markRead()}>标记工作台已读</button>
    <ConversationPaging page={current.page} pending={current.pagePending} onChange={current.changePage} />
  </>;
}
function Batch() {
  const current = useBatchManagement();
  useEffect(() => { batch = current; }, [current]);
  return <output>{current.selectedIds.join(",")}</output>;
}
function Workspace({ isBatch = false }: { isBatch?: boolean }) {
  const current = useAccount();
  useEffect(() => { account = current; }, [current]);
  if ((current.blocked && !current.suspended) || !current.snapshot?.capabilities.read_chat) return null;
  const Component = isBatch ? Batch : Chat;
  return <Component key={`${current.snapshot.account.epoch}:${current.generation}`} />;
}
async function mount(isBatch = false) {
  await act(async () => root.render(<AccountProvider><Workspace isBatch={isBatch} /></AccountProvider>));
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  mocks.backend.getConnection.mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.listConversations.mockResolvedValue(page());
  mocks.backend.getConversation.mockResolvedValue(detail());
  mocks.backend.getConversationRevision.mockResolvedValue(revision);
  mocks.backend.queryTranslations.mockResolvedValue({ translations: {} });
  mocks.backend.requestTranslations.mockResolvedValue({ task_id: "", status: "succeeded", message: "没有需要翻译的内容" });
  mocks.backend.getTranslationJob.mockResolvedValue(null);
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

describe("inbox workspace", () => {
  it("marks only on explicit click using the displayed snapshot, retaining newer unread and pending state", async () => {
    await mount();
    const textarea = container.querySelector("textarea");
    await act(async () => {
      chat.setDraft("keep reply");
      window.dispatchEvent(new Event("focus"));
    });
    expect(mocks.backend.markConversationRead).not.toHaveBeenCalled();
    const receipt = deferred<ReadReceipt>();
    mocks.backend.markConversationRead.mockReturnValueOnce(receipt.promise);
    await act(async () => container.querySelector("button")!.click());
    expect(mocks.backend.markConversationRead).toHaveBeenCalledExactlyOnceWith("42", "displayed-token");
    mocks.backend.getConversation.mockResolvedValue(detail("42", { readSnapshot: "later-token", unreadCount: 5 }));
    await act(async () => account.refreshReads());
    expect(chat.activeConversation?.readSnapshot).toBe("later-token");
    const pendingDetail = deferred<ConversationDetail>();
    mocks.backend.getConversation.mockReturnValueOnce(pendingDetail.promise);
    await act(async () => receipt.resolve({ state: { unread_count: 2, reply_state: "needs_reply", pending_since: 100, due_at: 86500, is_overdue: true, history_pending: false, uncertain: false, read_seq: 3, snapshot_seq: 3 }, inbox_revision: 2 }));
    expect(chat.activeConversation).toMatchObject({ unreadCount: 2, replyState: "needs_reply", pendingSince: 100, isOverdue: true });
    expect(container.textContent).toContain("本工作台未读 2");
    expect(container.textContent).toContain("待回复");
    expect(container.querySelector("textarea")).toBe(textarea);
    expect(chat.draft).toBe("keep reply");
    await act(async () => pendingDetail.resolve(detail("42", { unreadCount: 2, readSnapshot: "later-token" })));
  });

  it("keeps historical baseline unread at zero and separates history from timed pending", async () => {
    const historical = detail("42", { unreadCount: 0, replyState: "history_pending", historyPending: true, pendingSince: null, dueAt: null, isOverdue: false });
    mocks.backend.listConversations.mockResolvedValue(page([historical]));
    mocks.backend.getConversation.mockResolvedValue(historical);
    await mount();
    expect(container.textContent).toContain("本工作台未读 0");
    expect(container.textContent).toContain("历史待确认");
    expect(container.textContent).not.toContain("超时");
    expect(mocks.backend.markConversationRead).not.toHaveBeenCalled();
  });

  it("ignores a late filter response and preserves the active detail, draft and scroll outside the page", async () => {
    await mount();
    await act(async () => chat.setDraft("draft"));
    const textarea = container.querySelector("textarea")!;
    textarea.scrollTop = 60;
    const old = deferred<ConversationPage>();
    mocks.backend.listConversations.mockReturnValueOnce(old.promise);
    await act(async () => chat.changeQuery({ q: "older", reply_state: "needs_reply" }));
    mocks.backend.listConversations.mockResolvedValueOnce(page([detail("99")]));
    await act(async () => chat.changeQuery({ q: "new%_", search_scope: "messages", country: "ES", tag: "A" }));
    await act(async () => old.resolve(page([detail("88")])));
    expect(chat.conversations.map((item) => item.id)).toEqual(["99"]);
    expect(chat.activeConversation?.id).toBe("42");
    expect(chat.draft).toBe("draft");
    expect(container.querySelector("textarea")).toBe(textarea);
    expect(textarea.scrollTop).toBe(60);
    expect(mocks.backend.listConversations).toHaveBeenLastCalledWith({ q: "new%_", search_scope: "messages", country: "ES", tag: "A", offset: 0, limit: 50, pagination_revision: undefined });
  });

  it("passes the previous page token and resets expired pagination without appending or changing active detail", async () => {
    mocks.backend.listConversations.mockResolvedValueOnce(page([detail()], 0, 120, "token-a"));
    await mount();
    mocks.backend.listConversations.mockResolvedValueOnce(page([detail("90")], 50, 120, "token-b"));
    await act(async () => chat.changePage(50));
    expect(mocks.backend.listConversations).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, pagination_revision: "token-a" }));
    expect(chat.conversations.map((item) => item.id)).toEqual(["90"]);
    mocks.backend.listConversations.mockRejectedValueOnce(new ApiError("expired", "/api/conversations", { status: 409 })).mockResolvedValueOnce(page([detail("80")], 0, 120, "token-c"));
    await act(async () => chat.changePage(100));
    expect(mocks.backend.listConversations.mock.calls.at(-2)?.[0]).toMatchObject({ offset: 100, pagination_revision: "token-b" });
    expect(mocks.backend.listConversations).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0, pagination_revision: undefined }));
    expect(chat.conversations.map((item) => item.id)).toEqual(["80"]);
    expect(chat.page?.offset).toBe(0);
    expect(chat.activeConversation?.id).toBe("42");
  });

  it("clears batch selection on query/page changes and exports only the selected current page", async () => {
    mocks.backend.listConversations.mockResolvedValue(page([detail(), detail("43")], 0, 101));
    await mount(true);
    await act(async () => batch.selectAll());
    expect(batch.selectedIds).toEqual(["42", "43"]);
    mocks.backend.listConversations.mockResolvedValueOnce(page([detail("70")], 50, 101, "next-token"));
    await act(async () => batch.changePage(50));
    expect(batch.selectedIds).toEqual([]);
    await act(async () => batch.selectAll());
    mocks.backend.exportConversations.mockResolvedValue({ archive_name: "test.zip", content: "hello" });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    await act(async () => batch.exportSelected());
    expect(mocks.backend.exportConversations).toHaveBeenCalledExactlyOnceWith({ conversationIds: ["70"] });
    expect(mocks.backend.markConversationRead).not.toHaveBeenCalled();
    await act(async () => batch.changeQuery({ unread: true }));
    expect(batch.selectedIds).toEqual([]);
  });

  it("refreshes list and detail on inbox revision changes even at the same message revision", async () => {
    await mount();
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    mocks.backend.getConversationRevision.mockResolvedValue({ ...revision, inbox_revision: 2 });
    mocks.backend.listConversations.mockResolvedValue({ ...page(), inbox_revision: 2 });
    mocks.backend.getConversation.mockResolvedValue(detail("42", { unreadCount: 0 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    expect(chat.activeConversation?.unreadCount).toBe(0);
    expect(chat.activeConversation?.replyState).toBe("needs_reply");
  });

  it.each(["filter", "page"])("revalidates retained detail when a %s response observes a new inbox revision before polling", async (action) => {
    mocks.backend.listConversations.mockResolvedValueOnce(page([detail()], 0, 100));
    await mount();
    await act(async () => chat.setDraft("retain A draft"));
    const textarea = container.querySelector("textarea")!;
    textarea.scrollTop = 60;
    const displayed = chat.activeConversation;
    const nextDetail = deferred<ConversationDetail>();
    mocks.backend.getConversation.mockReturnValueOnce(nextDetail.promise);
    const offset = action === "page" ? 50 : 0;
    const nextPage = { ...page([detail("99")], offset, 100, "page-2"), inbox_revision: 2 };
    mocks.backend.listConversations.mockResolvedValue(nextPage);
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    await act(async () => {
      if (action === "page") chat.changePage(50);
      else chat.changeQuery({ country: "ES" });
    });
    expect(mocks.backend.getConversationRevision).not.toHaveBeenCalled();
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenLastCalledWith("42");
    expect(chat.activeConversation).toBe(displayed);
    expect(chat.detailLoading).toBe(false);
    expect(chat.refreshPending).toBe(true);
    await act(async () => nextDetail.resolve(detail("42", { unreadCount: 0, readSnapshot: "refreshed-A-token" })));
    expect(chat.activeConversation).toMatchObject({ id: "42", unreadCount: 0, replyState: "needs_reply", readSnapshot: "refreshed-A-token" });
    expect(chat.page).toEqual(nextPage);
    expect(chat.conversations.map((item) => item.id)).toEqual(["99"]);
    expect(chat.draft).toBe("retain A draft");
    expect(container.querySelector("textarea")).toBe(textarea);
    expect(textarea.scrollTop).toBe(60);
    expect(chat.refreshPending).toBe(false);
    mocks.backend.getConversationRevision.mockResolvedValue({ ...revision, inbox_revision: 2 });
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls + 1);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls + 1);
    expect(mocks.backend.markConversationRead).not.toHaveBeenCalled();
  });

  it("revalidates a retained deadline on a list read before polling without implicitly acknowledging its snapshot", async () => {
    const dueAt = Date.now() / 1000 + 5;
    const original = detail("42", { dueAt, isOverdue: false });
    mocks.backend.listConversations.mockResolvedValueOnce(page([original]));
    mocks.backend.getConversation.mockResolvedValueOnce(original);
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    const nextDetail = deferred<ConversationDetail>();
    mocks.backend.getConversation.mockReturnValueOnce(nextDetail.promise);
    mocks.backend.listConversations.mockResolvedValue(page([detail("99", { dueAt: null, isOverdue: false })]));
    await act(async () => chat.changeQuery({ tag: "other customer" }));
    expect(mocks.backend.getConversationRevision).not.toHaveBeenCalled();
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(2);
    expect(chat.activeConversation).toBe(original);
    expect(mocks.backend.markConversationRead).not.toHaveBeenCalled();
    const receipt = deferred<ReadReceipt>();
    mocks.backend.markConversationRead.mockReturnValueOnce(receipt.promise);
    await act(async () => container.querySelector("button")!.click());
    expect(mocks.backend.markConversationRead).toHaveBeenCalledExactlyOnceWith("42", "displayed-token");
    const refreshed = detail("42", { dueAt, isOverdue: true, readSnapshot: "new-deadline-token", unreadCount: 4 });
    mocks.backend.getConversation.mockResolvedValue(refreshed);
    await act(async () => nextDetail.resolve(refreshed));
    expect(chat.activeConversation).toMatchObject({ id: "42", isOverdue: true, unreadCount: 4, readSnapshot: "new-deadline-token" });
    await act(async () => { await vi.advanceTimersByTimeAsync(14_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(2);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(2);
    mocks.backend.getConversation.mockResolvedValue({ ...refreshed, unreadCount: 1 });
    await act(async () => receipt.resolve({ state: { unread_count: 1, reply_state: "needs_reply", pending_since: 100, due_at: dueAt, is_overdue: true, history_pending: false, uncertain: false, read_seq: 3, snapshot_seq: 3 }, inbox_revision: 1 }));
    expect(chat.activeConversation).toMatchObject({ unreadCount: 1, replyState: "needs_reply", pendingSince: 100, isOverdue: true });
    expect(mocks.backend.markConversationRead).toHaveBeenCalledExactlyOnceWith("42", "displayed-token");
  });

  it("does not let a late older poll erase the inbox revision already observed by a filtered list", async () => {
    await mount();
    const oldPoll = deferred<typeof revision>();
    mocks.backend.getConversationRevision.mockReturnValueOnce(oldPoll.promise);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    mocks.backend.listConversations.mockResolvedValue({ ...page([detail("99")]), inbox_revision: 2 });
    mocks.backend.getConversation.mockResolvedValue(detail("42", { unreadCount: 0 }));
    await act(async () => chat.changeQuery({ q: "other customer" }));
    expect(chat.activeConversation?.unreadCount).toBe(0);
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    await act(async () => oldPoll.resolve(revision));
    mocks.backend.getConversationRevision.mockResolvedValue({ ...revision, inbox_revision: 2 });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
    expect(chat.activeConversation?.id).toBe("42");
  });

  it("keeps the retained detail's deadline after a list read consumes an earlier conversation deadline", async () => {
    const now = Date.now() / 1000;
    const active = detail("42", { dueAt: now + 8, isOverdue: false });
    const earlier = detail("43", { dueAt: now + 5, isOverdue: false });
    mocks.backend.listConversations.mockResolvedValueOnce(page([active, earlier]));
    mocks.backend.getConversation.mockResolvedValue(active);
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    mocks.backend.listConversations.mockResolvedValue(page([detail("99", { dueAt: null, isOverdue: false })]));
    await act(async () => chat.changeQuery({ country: "ES" }));
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(2);
    expect(chat.activeConversation).toMatchObject({ id: "42", dueAt: now + 8, isOverdue: false });
    mocks.backend.getConversation.mockResolvedValue({ ...active, isOverdue: true, readSnapshot: "after-deadline" });
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(chat.activeConversation).toMatchObject({ id: "42", isOverdue: true, readSnapshot: "after-deadline" });
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(3);
    expect(mocks.backend.markConversationRead).not.toHaveBeenCalled();
  });

  it("retries a failed detail revalidation at the inbox revision already observed by the list", async () => {
    await mount();
    const displayed = chat.activeConversation;
    mocks.backend.listConversations.mockResolvedValue({ ...page([detail("99")]), inbox_revision: 2 });
    mocks.backend.getConversation.mockRejectedValueOnce(new Error("detail offline"));
    await act(async () => chat.changeQuery({ country: "ES" }));
    expect(chat.activeConversation).toBe(displayed);
    expect(chat.refreshError).toBe(true);
    expect(chat.refreshPending).toBe(false);
    mocks.backend.getConversationRevision.mockResolvedValue({ ...revision, inbox_revision: 2 });
    mocks.backend.getConversation.mockResolvedValue(detail("42", { unreadCount: 0 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(chat.activeConversation).toMatchObject({ id: "42", unreadCount: 0 });
    expect(chat.refreshError).toBe(false);
    const listCalls = mocks.backend.listConversations.mock.calls.length;
    const detailCalls = mocks.backend.getConversation.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(listCalls);
    expect(mocks.backend.getConversation).toHaveBeenCalledTimes(detailCalls);
  });

  it("crosses a deadline without a new message or inbox revision", async () => {
    const dueAt = Date.now() / 1000 + 15;
    mocks.backend.listConversations.mockResolvedValue(page([detail("42", { dueAt, isOverdue: false })]));
    mocks.backend.getConversation.mockResolvedValue(detail("42", { dueAt, isOverdue: false }));
    mocks.backend.getConversationRevision.mockResolvedValue({ ...revision, next_due_at: dueAt });
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    const calls = mocks.backend.listConversations.mock.calls.length;
    mocks.backend.listConversations.mockResolvedValue(page([detail("42", { dueAt, isOverdue: true })]));
    mocks.backend.getConversation.mockResolvedValue(detail("42", { dueAt, isOverdue: true }));
    mocks.backend.getConversationRevision.mockResolvedValue(revision);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(calls + 1);
    expect(chat.activeConversation?.isOverdue).toBe(true);
  });

  it("refreshes an initially empty overdue filter and keeps failed or suspended reads from replacing the workspace", async () => {
    await mount();
    const textarea = container.querySelector("textarea");
    await act(async () => chat.setDraft("saved draft"));
    mocks.backend.listConversations.mockResolvedValueOnce(page([]));
    await act(async () => chat.changeQuery({ overdue: true }));
    expect(chat.conversations).toEqual([]);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(chat.conversations).toHaveLength(1);
    mocks.backend.markConversationRead.mockRejectedValueOnce(new Error("offline"));
    await act(async () => chat.markRead());
    expect(chat.activeConversation?.unreadCount).toBe(3);
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await account.refresh(); });
    await act(async () => chat.markRead());
    expect(mocks.backend.markConversationRead).toHaveBeenCalledTimes(1);
    expect(container.querySelector("textarea")).toBe(textarea);
    expect(chat.draft).toBe("saved draft");
  });

  it("rejects late read acknowledgements and filtered pages after an account switch", async () => {
    await mount();
    const read = deferred<ReadReceipt>();
    const oldPage = deferred<ConversationPage>();
    mocks.backend.markConversationRead.mockReturnValueOnce(read.promise);
    mocks.backend.listConversations.mockReturnValueOnce(oldPage.promise);
    await act(async () => { void chat.markRead(); chat.changeQuery({ q: "old" }); });
    mocks.backend.getConnection.mockResolvedValue({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "seller-b", self_ali_id: "b" } });
    mocks.backend.getConversation.mockResolvedValue(detail("99"));
    mocks.backend.listConversations.mockResolvedValue(page([detail("99")]));
    await act(async () => { await account.refresh(); });
    const reads = account.readRefreshSequence;
    await act(async () => {
      oldPage.resolve(page());
      read.resolve({ state: { unread_count: 0, reply_state: "none", pending_since: null, due_at: null, is_overdue: false, history_pending: false, uncertain: false, read_seq: 3, snapshot_seq: 3 }, inbox_revision: 99 });
    });
    expect(chat.activeConversation?.id).toBe("99");
    expect(chat.conversations.map((item) => item.id)).toEqual(["99"]);
    expect(account.readRefreshSequence).toBe(reads);
  });

  it("coalesces deadline-filtered reads while a previous page read is still pending", async () => {
    await mount();
    await act(async () => chat.changeQuery({ overdue: true }));
    const slowPage = deferred<ConversationPage>();
    mocks.backend.listConversations.mockReturnValueOnce(slowPage.promise);
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    const calls = mocks.backend.listConversations.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(mocks.backend.listConversations).toHaveBeenCalledTimes(calls);
    await act(async () => slowPage.resolve(page([detail("99")])));
    expect(chat.conversations.map((item) => item.id)).toEqual(["99"]);
    expect(chat.activeConversation?.id).toBe("42");
  });
});
