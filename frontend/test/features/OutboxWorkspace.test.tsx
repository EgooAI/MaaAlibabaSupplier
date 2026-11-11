// @vitest-environment happy-dom
import { act, useEffect, useState, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { ChatConversation } from "@/features/chat/ChatConversation";
import { intentKey } from "@/features/chat/outbox/intentStorage";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import { adaptConversationDetail } from "@/services/chatAdapter";
import type { OutboxTask, SendMessageInput } from "@/types/chatOperations";
import type { ConversationDetail } from "@/types/chatCanonical";
import { outboxTask } from "@/test/support/outboxFixture";
import { ApiError } from "@/services/httpAdapter";
import { useChatWorkbench } from "@/features/chat/hooks/useChatWorkbench";
import { authenticatedSession } from "@/test/support/authFixture";

const mocks = vi.hoisted(() => ({ backend: {
  getConnection: vi.fn(), listOutbox: vi.fn(), getOutbox: vi.fn(), sendMessage: vi.fn(), cancelOutbox: vi.fn(), retryOutbox: vi.fn(),
  listConversations: vi.fn(), getConversation: vi.fn(), getConversationRevision: vi.fn(), markConversationRead: vi.fn(),
  queryTranslations: vi.fn(), requestTranslations: vi.fn(), getTranslationJob: vi.fn(), listPortraitTags: vi.fn(),
} }));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => {
  const Text = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  const Button = ({ children, disabled, loading, onClick, "aria-label": label }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button aria-label={label} disabled={disabled || loading} onClick={onClick}>{children}</button>;
  const Popover = ({ children, content }: { children: ReactNode; content: ReactNode }) => {
    const [open, setOpen] = useState(false);
    return <><span onClick={() => setOpen(!open)}>{children}</span>{open ? <aside>{content}</aside> : null}</>;
  };
  return {
    App: { useApp: () => ({ message: { warning: vi.fn(), error: vi.fn(), success: vi.fn() } }) },
    Space: Text, Tag: Text, Avatar: Text, Typography: { Text, Paragraph: Text }, Button, Popover, Popconfirm: Text, Tooltip: Text, Spin: Text,
    Empty: Object.assign(({ description }: { description: string }) => <div>{description}</div>, { PRESENTED_IMAGE_SIMPLE: null }),
    Drawer: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <aside>{children}</aside> : null,
    Alert: ({ title, action }: { title: ReactNode; action: ReactNode }) => <div role="alert">{title}{action}</div>,
  };
});
vi.mock("@/components/MessageComposer", () => ({ MessageComposer: ({ value, onSend, onFill, sendDisabled, loading }: {
  value: string; onSend: () => void; onFill: () => void; sendDisabled: boolean; loading: boolean;
}) => <><textarea value={value} readOnly /><button disabled={sendDisabled || loading || !value.trim()} onClick={onSend}>发送</button><button disabled={sendDisabled || loading || !value.trim()} onClick={onFill}>仅填入客户端</button></> }));

let root: Root;
let container: HTMLDivElement;
let store: OutboxTask[];
let detail: ConversationDetail;
let chat: ReturnType<typeof useChatWorkbench>;
const ready = () => ({ ...structuredClone(connectionSnapshot), client: { ...connectionSnapshot.client, connected: true }, capabilities: { read_chat: true, use_ai: true, operate_client: true } });

function Harness() {
  const account = useAccount();
  if (!account.snapshot || (account.blocked && !account.suspended)) return null;
  return <Workspace key={`${account.snapshot.account.epoch}:${account.generation}`} />;
}
function Workspace() {
  const workbench = useChatWorkbench();
  useEffect(() => { chat = workbench; }, [workbench]);
  return workbench.activeConversation ? <ChatConversation key={workbench.activeConversation.id} active={workbench.activeConversation} workbench={workbench} onFork={() => {}} onAnalysis={() => {}} /> : null;
}
const mount = async () => { await act(async () => root.render(<AccountProvider><Harness /></AccountProvider>)); };
const edit = async (text: string) => { await act(async () => chat.setDraft(text)); };
const start = async () => { await mount(); await edit("submitted text"); };
const button = (text: string) => [...container.querySelectorAll("button")].find((item) => item.textContent === text);
const click = async (text: string) => { expect(button(text), text).toBeDefined(); await act(async () => button(text)!.click()); };
const tick = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); };
const reload = async () => { await act(async () => root.unmount()); root = createRoot(container); await mount(); };
const taskBubble = (id = "outbox-1") => container.querySelector(`[aria-label="发送任务 ${id}"]`);
const intents = () => JSON.parse(localStorage.getItem(intentKey(connectionSnapshot.account, "42")) ?? "[]");
const showDetails = async (id = "outbox-1") => { await act(async () => (taskBubble(id)!.querySelector('[aria-label="发送任务详情"]') as HTMLButtonElement).click()); };
const recentTasks = () => structuredClone([...store].sort((a, b) => b.created_at - a.created_at).slice(0, 100));
const addRecentTasks = () => store.push(...Array.from({ length: 101 }, (_, i) => outboxTask({ id: `recent-${i}`, idempotency_key: `recent-${i}`, content: `recent ${i}`, created_at: 2000 + i, status: "cancelled" })));

beforeEach(async () => {
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  store = [];
  detail = adaptConversationDetail({ sid: 42, name: "Buyer", participants: [], messages: [], latest: { content: "", updated_at: null }, unread_count: 0, reply_state: "waiting_customer", pending_since: null, history_pending: false, uncertain: false, read_snapshot: "snapshot" });
  detail.customer.loginId = "buyer-login";
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  mocks.backend.getConnection.mockResolvedValue(ready());
  mocks.backend.listConversations.mockImplementation(async () => ({ items: [detail], total: 1, offset: 0, limit: 50, inbox_revision: 1, pagination_revision: "page-1" }));
  mocks.backend.getConversation.mockImplementation(async (id: string) => ({ ...detail, id }));
  mocks.backend.getConversationRevision.mockResolvedValue({ ready: true, revision: 1, inbox_revision: 1 });
  mocks.backend.listPortraitTags.mockResolvedValue({ tags: [] });
  mocks.backend.queryTranslations.mockResolvedValue({ translations: {} });
  mocks.backend.listOutbox.mockImplementation(async (sid: string) => structuredClone(store.filter((task) => String(task.conversation_id) === sid)));
  mocks.backend.getOutbox.mockImplementation(async (id: string) => structuredClone(store.find((task) => task.id === id)!));
  mocks.backend.sendMessage.mockImplementation(async (input: SendMessageInput) => {
    const existing = store.find((task) => task.idempotency_key === input.idempotency_key);
    const task = existing ?? outboxTask({ id: input.idempotency_key, conversation_id: Number(input.conversationId), content: input.content, action: input.action, idempotency_key: input.idempotency_key });
    if (!existing) store.push(task);
    return { outbox: structuredClone(task) };
  });
  mocks.backend.cancelOutbox.mockImplementation(async (id: string) => {
    const task = store.find((item) => item.id === id)!;
    Object.assign(task, { status: "cancelled", version: task.version + 1 });
    return structuredClone(task);
  });
  mocks.backend.retryOutbox.mockImplementation(async (id: string) => {
    const task = store.find((item) => item.id === id)!;
    Object.assign(task, { status: "queued", version: task.version + 1, attempt: task.attempt + 1 });
    return structuredClone(task);
  });
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

describe("inline outbox workflow", () => {
  it.each(["send", "test"] as const)("submits %s once without confirmation and clears only after acceptance", async (action) => {
    await start();
    let finish!: (value: { outbox: OutboxTask }) => void;
    mocks.backend.sendMessage.mockImplementationOnce((input: SendMessageInput) => {
      expect(intents()).toContainEqual(expect.objectContaining({ key: input.idempotency_key, content: input.content, draftId: expect.any(String) }));
      return new Promise((resolve) => { finish = resolve; });
    });
    const label = action === "send" ? "发送" : "仅填入客户端";
    await act(async () => { button(label)!.click(); button(label)!.click(); });
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
    expect(chat.draft).toBe("submitted text");
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    const input = mocks.backend.sendMessage.mock.calls[0][0];
    await act(async () => finish({ outbox: outboxTask({ action, idempotency_key: input.idempotency_key }) }));
    expect(chat.draft).toBe("");
    expect(Boolean(taskBubble())).toBe(action === "send");
    expect(container.textContent).toContain(action === "send" ? "等待发送" : "等待填入");
    expect(container.textContent).not.toContain("发送记录");
  });

  it.each(["new reply", "submitted text"])("preserves edits made while the receipt is pending, including an ABA edit: %s", async (latest) => {
    await start();
    let finish!: (value: { outbox: OutboxTask }) => void;
    mocks.backend.sendMessage.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await click("发送");
    await edit("intermediate edit");
    await edit(latest);
    const input = mocks.backend.sendMessage.mock.calls[0][0];
    await act(async () => finish({ outbox: outboxTask({ idempotency_key: input.idempotency_key }) }));
    expect(chat.draft).toBe(latest);
    expect(taskBubble()?.textContent).toContain("submitted text");
  });

  it("allows a new draft with identical content while a timeout recovers the original key", async () => {
    await start();
    mocks.backend.sendMessage.mockImplementationOnce(async (input: SendMessageInput) => {
      store.push(outboxTask({ idempotency_key: input.idempotency_key }));
      throw new Error("timeout");
    });
    await click("发送");
    const original = mocks.backend.sendMessage.mock.calls[0][0];
    expect(chat.draft).toBe("submitted text");
    await reload();
    await click("发送");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
    expect(chat.draft).toBe("");
    await edit("submitted text");
    await click("发送");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(2);
    expect(mocks.backend.sendMessage.mock.calls[1][0].idempotency_key).not.toBe(original.idempotency_key);
  });

  it("recovers an unlisted timeout or busy response with its original key after reload", async () => {
    await start();
    mocks.backend.sendMessage.mockRejectedValueOnce(new ApiError("客户端繁忙", "/api/conversations/42/messages", { status: 409 }));
    await click("发送");
    const original = mocks.backend.sendMessage.mock.calls[0][0];
    expect(container.textContent).toContain("提交冲突（409）");
    expect(container.querySelector("article")).toBeNull();
    await reload();
    await click("恢复原提交");
    expect(mocks.backend.sendMessage.mock.calls[1][0]).toEqual(original);
    expect(chat.draft).toBe("");
  });

  it("keeps a new draft when explicitly recovering an older unresolved intent", async () => {
    await start();
    mocks.backend.sendMessage.mockRejectedValueOnce(new Error("timeout"));
    await click("发送");
    const original = mocks.backend.sendMessage.mock.calls[0][0];
    await edit("next reply");
    await click("恢复原提交");
    expect(mocks.backend.sendMessage.mock.calls[1][0]).toEqual(original);
    expect(chat.draft).toBe("next reply");
  });

  it.each(["failed", "cancelled"] as const)("preserves the draft when a receipt is already %s", async (status) => {
    await start();
    mocks.backend.sendMessage.mockImplementationOnce(async (input: SendMessageInput) => ({ outbox: outboxTask({ status, idempotency_key: input.idempotency_key }) }));
    await click("发送");
    expect(chat.draft).toBe("submitted text");
  });

  it("does not clear another conversation's draft after a delayed receipt", async () => {
    await start();
    let finish!: (receipt: { outbox: OutboxTask }) => void;
    mocks.backend.sendMessage.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await click("发送");
    const input = mocks.backend.sendMessage.mock.calls[0][0];
    await act(async () => chat.selectConversation("43"));
    await edit("other conversation draft");
    await act(async () => finish({ outbox: outboxTask({ idempotency_key: input.idempotency_key }) }));
    expect(chat.draft).toBe("other conversation draft");
    expect(taskBubble()).toBeNull();
    await act(async () => chat.selectConversation("42"));
    expect(chat.draft).toBe("submitted text");
  });

  it.each(["verifying", "unknown"] as const)("merges a %s task only after observation and its exact message is loaded", async (status) => {
    vi.useFakeTimers();
    store = [outboxTask({ status, may_have_sent: true })];
    await start();
    store[0] = { ...store[0], status: "observed", version: 2, matched_message_id: "messages:7" };
    await tick();
    expect(taskBubble()?.textContent).toContain("本地已匹配，等待同步");
    detail.messages = [{ id: "other", role: "seller", content: "submitted text", createdAt: new Date().toISOString(), externalMid: "alibaba_icbu:other:messages:7" }];
    await act(async () => chat.refreshActiveDetail());
    expect(taskBubble()).not.toBeNull();
    detail.messages[0] = { ...detail.messages[0], id: "actual", content: " submitted text", externalMid: `alibaba_icbu:${connectionSnapshot.account.self_ali_id}:messages:7` };
    await act(async () => chat.refreshActiveDetail());
    expect(taskBubble()).toBeNull();
    expect(container.querySelectorAll("[data-message-id]")).toHaveLength(1);
    expect(container.textContent).not.toContain("发送成功");
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  });

  it.each(["failed", "unknown", "running", "queued", "navigating", "verifying", "observed"] as const)("keeps %s inline with gated actions and collapsed details", async (status) => {
    store = [outboxTask({ status, reason: "message_not_observed" })];
    await mount();
    expect(taskBubble()?.textContent).toContain("submitted text");
    expect(container.textContent).not.toContain("尚未在本地消息");
    expect(Boolean(button("重试"))).toBe(status === "failed");
    await showDetails();
    expect(container.textContent).toContain("尚未在本地消息");
    expect(Boolean(button("取消任务"))).toBe(status === "queued");
    if (status === "failed") {
      await click("重试");
      expect(mocks.backend.retryOutbox).toHaveBeenCalledExactlyOnceWith("outbox-1", 1);
      expect(container.querySelector('[role="dialog"]')).toBeNull();
    }
  });

  it("shows filled tasks outside the timeline and removes cancelled bubbles", async () => {
    store = [outboxTask({ action: "test", status: "filled" }), outboxTask({ id: "cancel-me", idempotency_key: "cancel-key" })];
    await mount();
    expect(taskBubble()).toBeNull();
    expect(container.textContent).toContain("已填入（未发送）");
    await showDetails("cancel-me");
    await click("取消任务");
    expect(taskBubble("cancel-me")).toBeNull();
    expect(container.textContent).toContain("任务已取消");
    expect(store).toHaveLength(2);
  });

  it("blocks risky retries but allows queued cancellation while disconnected", async () => {
    store = [outboxTask({ status: "failed", may_have_sent: true }), outboxTask({ id: "cancel-me", idempotency_key: "other" })];
    mocks.backend.getConnection.mockResolvedValue(connectionSnapshot);
    await mount();
    expect(button("重试")).toBeUndefined();
    expect(button("发送")!.disabled).toBe(true);
    await showDetails("cancel-me");
    expect(button("取消任务")!.disabled).toBe(false);
    await click("取消任务");
    expect(mocks.backend.cancelOutbox).toHaveBeenCalledExactlyOnceWith("cancel-me", 1);
  });

  it("blocks submission when durable storage fails and preserves drafts", async () => {
    await start();
    const original = localStorage;
    vi.stubGlobal("localStorage", { getItem: (key: string) => original.getItem(key), setItem: () => { throw new Error("quota"); } });
    await click("发送");
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
    expect(chat.draft).toBe("submitted text");
    vi.stubGlobal("localStorage", original);
    await click("刷新任务");
    await click("发送");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("persists the intent before lookup and keeps its key after a failed lookup", async () => {
    await start();
    mocks.backend.listOutbox.mockImplementationOnce(async () => { expect(intents()).toHaveLength(1); throw new Error("lookup failed"); });
    await click("发送");
    const key = intents()[0].key;
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
    await click("发送");
    expect(mocks.backend.sendMessage.mock.calls[0][0].idempotency_key).toBe(key);
  });

  it("polls only visible workspaces and keeps task state during account suspension", async () => {
    vi.useFakeTimers();
    store = [outboxTask()];
    await start();
    const initial = mocks.backend.listOutbox.mock.calls.length;
    Object.defineProperty(document, "hidden", { value: true });
    await tick();
    expect(mocks.backend.listOutbox).toHaveBeenCalledTimes(initial);
    Object.defineProperty(document, "hidden", { value: false });
    await tick();
    expect(mocks.backend.listOutbox).toHaveBeenCalledTimes(initial + 1);
    await act(async () => accountSession.suspend());
    expect(taskBubble()).not.toBeNull();
    expect(button("发送")!.disabled).toBe(true);
    await tick();
    expect(mocks.backend.listOutbox).toHaveBeenCalledTimes(initial + 1);
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  });

  it("keeps polling an older unknown task beyond the recent list and recovers its ID after reload", async () => {
    vi.useFakeTimers();
    store = [outboxTask({ status: "failed", created_at: 1 })];
    mocks.backend.listOutbox.mockImplementation(async () => recentTasks());
    await mount();
    await click("重试");
    addRecentTasks();
    store[0] = { ...store[0], status: "unknown", version: 3, may_have_sent: true };
    await tick();
    expect(taskBubble()?.textContent).toContain("结果未知");
    await reload();
    expect(taskBubble()?.textContent).toContain("结果未知");
    store[0] = { ...store[0], status: "observed", version: 4, matched_message_id: "messages:late" };
    await tick();
    expect(taskBubble()?.textContent).toContain("本地已匹配");
    expect(mocks.backend.getOutbox).toHaveBeenCalledWith("outbox-1");
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  });

  it("merges stale poll responses monotonically after cancellation", async () => {
    vi.useFakeTimers();
    store = [outboxTask()];
    await mount();
    const older = structuredClone(store);
    let finish!: (tasks: OutboxTask[]) => void;
    mocks.backend.listOutbox.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await tick();
    await showDetails();
    await click("取消任务");
    await act(async () => finish(older));
    expect(taskBubble()).toBeNull();
    expect(container.textContent).toContain("任务已取消");
  });

  it.each(["seller", "directory"])("discards old responses and isolates intents across %s changes", async (scope) => {
    await start();
    let finish!: (receipt: { outbox: OutboxTask }) => void;
    mocks.backend.sendMessage.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await click("发送");
    const input = mocks.backend.sendMessage.mock.calls[0][0];
    const next = { ...ready(), account: { ...connectionSnapshot.account, epoch: "b", ...(scope === "seller" ? { self_ali_id: "other" } : { data_dir: "D:/other" }) } };
    await act(async () => accountSession.accept(next));
    await edit("new account draft");
    await act(async () => finish({ outbox: outboxTask({ idempotency_key: input.idempotency_key }) }));
    expect(taskBubble()).toBeNull();
    expect(chat.draft).toBe("new account draft");
    expect(localStorage.getItem(intentKey(next.account, "42"))).toBeNull();
    expect(intents()).toHaveLength(1);
  });

  it.each([{ id: "wrong" }, { conversation_id: 43 }, { idempotency_key: "wrong" }])("rejects mismatched supplementary records: %j", async (mismatch) => {
    localStorage.setItem(intentKey(connectionSnapshot.account, "42"), JSON.stringify([{ key: "intent-1", content: "submitted text", action: "send", taskId: "outbox-1" }]));
    mocks.backend.getOutbox.mockResolvedValue(outboxTask(mismatch));
    await mount();
    expect(container.textContent).toContain("任务读取失败");
    expect(taskBubble()).toBeNull();
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  });
});
