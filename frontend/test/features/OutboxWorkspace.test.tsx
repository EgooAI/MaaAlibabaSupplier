// @vitest-environment happy-dom
import { act, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { OutboxWorkspace } from "@/features/chat/outbox/OutboxWorkspace";
import { intentKey } from "@/features/chat/outbox/intentStorage";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import { adaptConversationDetail } from "@/services/chatAdapter";
import type { OutboxTask, SendMessageInput } from "@/types/chatOperations";
import { outboxTask, screenshotPng } from "@/test/support/outboxFixture";
import { ApiError } from "@/services/httpAdapter";

const mocks = vi.hoisted(() => ({
  backend: { getConnection: vi.fn(), listOutbox: vi.fn(), getOutbox: vi.fn(), sendMessage: vi.fn(), confirmOutbox: vi.fn(), cancelOutbox: vi.fn(), retryOutbox: vi.fn(), getOutboxScreenshot: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => {
  const Text = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  const Button = ({ children, disabled, loading, onClick }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>;
  return {
    App: { useApp: () => ({ message: { warning: vi.fn() } }) },
    Space: Text, Tag: Text, Typography: { Text, Paragraph: Text }, Button,
    Alert: ({ title }: { title: ReactNode }) => <div role="alert">{title}</div>,
    Modal: ({ open, title, children, footer, onCancel, onOk, okText, okButtonProps }: { open: boolean; title: string; children: ReactNode; footer?: ReactNode; onCancel: () => void; onOk?: () => void; okText?: string; okButtonProps?: { disabled: boolean } }) => open ? <section role="dialog" aria-label={title}>{children}<button onClick={onCancel}>关闭弹窗</button>{footer ?? <button disabled={okButtonProps?.disabled} onClick={onOk}>{okText}</button>}</section> : null,
  };
});
vi.mock("@/components/MessageComposer", () => ({ MessageComposer: ({ value, onSend, sendDisabled, loading }: { value: string; onSend: () => void; sendDisabled: boolean; loading: boolean }) => <><textarea value={value} readOnly /><button disabled={sendDisabled || loading} onClick={onSend}>发送入口</button></> }));

let root: Root;
let container: HTMLDivElement;
let store: OutboxTask[];
let createUrl: ReturnType<typeof vi.fn>;
let revokeUrl: ReturnType<typeof vi.fn>;
const readySnapshot = () => ({ ...structuredClone(connectionSnapshot), client: { ...connectionSnapshot.client, connected: true, confirmed: true }, capabilities: { read_chat: true, use_ai: true, operate_client: true } });
const awaiting = (overrides: Partial<OutboxTask> = {}) => outboxTask({ status: "awaiting_confirmation", version: 3, screenshot_id: "frame-1", screenshot_at: Date.now() / 1000, ...overrides });

function Harness({ sid = "42", draft = "submitted text" }: { sid?: string; draft?: string }) {
  const account = useAccount();
  if (!account.snapshot || (account.blocked && !account.suspended)) return null;
  const conversation = adaptConversationDetail({ sid: Number(sid), name: "Buyer", participants: [], messages: [], latest: { content: "", updated_at: null }, unread_count: 0, status: "following", priority: "medium" });
  conversation.customer.name = "Recipient Name";
  conversation.customer.loginId = "buyer-login";
  return <OutboxWorkspace key={`${account.generation}:${sid}`} conversation={conversation} value={draft} onChange={() => {}} translationVisible onToggleTranslation={() => {}} onRetranslate={() => {}} onOpenSuggestions={() => {}} onOpenIntentAnalysis={() => {}} onOpenStageAnalysis={() => {}} />;
}

beforeEach(() => {
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  store = [];
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  mocks.backend.getConnection.mockResolvedValue(readySnapshot());
  mocks.backend.listOutbox.mockImplementation(async (sid: string) => structuredClone(store.filter((task) => String(task.conversation_id) === sid)));
  mocks.backend.getOutbox.mockImplementation(async (id: string) => structuredClone(store.find((task) => task.id === id)!));
  mocks.backend.getOutboxScreenshot.mockImplementation(async () => screenshotPng());
  mocks.backend.sendMessage.mockImplementation(async (input: SendMessageInput) => {
    const task = outboxTask({ id: input.idempotency_key, conversation_id: Number(input.conversationId), content: input.content, action: input.action, idempotency_key: input.idempotency_key });
    store.push(task);
    return { outbox: structuredClone(task) };
  });
  mocks.backend.confirmOutbox.mockImplementation(async (id: string) => {
    const task = store.find((item) => item.id === id)!;
    Object.assign(task, { status: "queued_send", version: task.version + 1 });
    return structuredClone(task);
  });
  mocks.backend.cancelOutbox.mockImplementation(async (id: string) => {
    const task = store.find((item) => item.id === id)!;
    Object.assign(task, { status: "cancelled", version: task.version + 1 });
    return structuredClone(task);
  });
  mocks.backend.retryOutbox.mockImplementation(async (id: string) => {
    const task = store.find((item) => item.id === id)!;
    Object.assign(task, { status: "queued", version: task.version + 1, screenshot_id: null });
    return structuredClone(task);
  });
  createUrl = vi.fn().mockImplementation(() => `blob:test-${createUrl.mock.calls.length}`);
  revokeUrl = vi.fn();
  vi.stubGlobal("URL", Object.assign(class extends URL {}, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }));
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

const render = async (props: { sid?: string; draft?: string } = {}) => { await act(async () => root.render(<AccountProvider><Harness {...props} /></AccountProvider>)); };
const button = (text: string) => [...container.querySelectorAll("button")].find((item) => item.textContent === text)!;
const click = async (text: string) => { await act(async () => button(text).click()); };
const loadImage = async () => { await act(async () => container.querySelector("img")!.dispatchEvent(new Event("load"))); };
const tick = async (ms = 2000) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const recentTasks = () => structuredClone([...store].sort((a, b) => b.created_at - a.created_at).slice(0, 100));
const addRecentTasks = () => store.push(...Array.from({ length: 101 }, (_, i) => outboxTask({ id: `recent-${i}`, idempotency_key: `recent-key-${i}`, content: `recent text ${i}`, created_at: 1000 + i, status: "observed" })));

describe("outbox workbench", () => {
  it.each(["send", "test"] as const)("freezes %s content, persists before POST, coalesces double clicks and requires a loaded PNG before confirming", async (action) => {
    await render();
    await click("发送入口");
    const dialog = container.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("Recipient Name");
    expect(dialog.textContent).toContain("buyer-login");
    expect(dialog.textContent).toContain(connectionSnapshot.account.self_ali_id);
    await render({ draft: "newer draft must survive" });
    expect(dialog.textContent).not.toContain("newer draft");
    mocks.backend.sendMessage.mockImplementationOnce(async (input: SendMessageInput) => {
      expect(JSON.parse(localStorage.getItem(intentKey(connectionSnapshot.account, "42"))!)).toContainEqual({ key: input.idempotency_key, content: "submitted text", action });
      const task = awaiting({ idempotency_key: input.idempotency_key, action });
      store.push(task);
      return { outbox: task };
    });
    const startLabel = action === "send" ? "确认收件人，开始搜索（发送）" : "确认收件人，开始搜索（仅填入）";
    const confirmLabel = action === "send" ? "确认该联系人，发送" : "确认该联系人，仅填入";
    await act(async () => { button(startLabel).click(); button(startLabel).click(); });
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
    expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
    await click("查看截图并确认联系人");
    expect(mocks.backend.getOutbox).toHaveBeenCalledWith("outbox-1");
    expect(mocks.backend.getOutboxScreenshot).toHaveBeenCalledExactlyOnceWith("outbox-1", "frame-1", 3);
    expect(container.querySelector("img")?.src).toBe("blob:test-1");
    expect(button(confirmLabel).disabled).toBe(true);
    await loadImage();
    await act(async () => { button(confirmLabel).click(); button(confirmLabel).click(); });
    expect(mocks.backend.confirmOutbox).toHaveBeenCalledExactlyOnceWith("outbox-1", 3, "frame-1");
    expect(container.querySelector("textarea")?.value).toBe("newer draft must survive");
    expect(revokeUrl).toHaveBeenCalledWith("blob:test-1");
  });

  it("recovers a timed-out submission on reload through GET without creating another send", async () => {
    await render();
    mocks.backend.sendMessage.mockImplementationOnce(async (input: SendMessageInput) => {
      store.push(outboxTask({ idempotency_key: input.idempotency_key }));
      throw new Error("HTTP timeout");
    });
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    const key = mocks.backend.sendMessage.mock.calls[0][0].idempotency_key;
    expect(container.textContent).toContain("提交结果未知");
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    expect(container.textContent).toContain("等待搜索");
    expect(button("恢复原提交")).toBeUndefined();
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
    expect(store[0].idempotency_key).toBe(key);
    store[0] = { ...store[0], status: "observed", version: 9 };
    await click("刷新任务");
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
    await click("新建发送");
    expect(container.textContent).toContain("独立的新任务");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(2);
    expect(mocks.backend.sendMessage.mock.calls[1][0].idempotency_key).not.toBe(key);
  });

  it("retains the same persisted key when a timeout has no discoverable server record", async () => {
    await render();
    mocks.backend.sendMessage.mockRejectedValueOnce(new Error("timeout"));
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    const original = mocks.backend.sendMessage.mock.calls[0][0];
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    await click("恢复原提交");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage.mock.calls[1][0]).toEqual(original);
  });

  it("blocks submission on storage failure and permits recovery after storage is repaired", async () => {
    vi.useFakeTimers();
    await render();
    const original = localStorage;
    vi.stubGlobal("localStorage", { getItem: (key: string) => original.getItem(key), setItem: () => { throw new Error("quota"); } });
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    expect(container.textContent).toContain("已禁止提交");
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
    expect(container.querySelector("textarea")?.value).toBe("submitted text");
    await tick();
    expect(container.textContent).toContain("已禁止新提交");
    expect(button("确认收件人，开始搜索（发送）").disabled).toBe(true);
    vi.stubGlobal("localStorage", original);
    await click("刷新任务");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("polls only while visible, preserves background records across conversations and does not invoke GUI operations", async () => {
    vi.useFakeTimers();
    store = [outboxTask(), outboxTask({ id: "other", conversation_id: 43, content: "other conversation" })];
    await render();
    expect(mocks.backend.listOutbox).toHaveBeenCalledTimes(1);
    await tick();
    expect(mocks.backend.listOutbox).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "hidden", { value: true });
    await tick(6000);
    expect(mocks.backend.listOutbox).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "hidden", { value: false });
    await act(async () => document.dispatchEvent(new Event("visibilitychange")));
    expect(mocks.backend.listOutbox).toHaveBeenCalledTimes(3);
    await render({ sid: "43" });
    expect(container.textContent).toContain("other conversation");
    expect(container.querySelector('[aria-label="任务 outbox-1"]')).toBeNull();
    store[0] = { ...store[0], status: "unknown", version: 4 };
    await render();
    expect(container.textContent).toContain("结果未知");
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
    expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
    expect(mocks.backend.getOutboxScreenshot).not.toHaveBeenCalled();
  });

  it("asks again for a changed screenshot after queued_send and never carries confirmation forward", async () => {
    vi.useFakeTimers();
    store = [awaiting()];
    await render();
    await click("查看截图并确认联系人");
    await loadImage();
    await click("确认该联系人，发送");
    expect(container.querySelector("img")).toBeNull();
    store[0] = awaiting({ version: 5, screenshot_id: "frame-2" });
    await tick();
    expect(mocks.backend.getOutboxScreenshot).toHaveBeenLastCalledWith("outbox-1", "frame-2", 5);
    expect(button("确认该联系人，发送").disabled).toBe(true);
    expect(mocks.backend.confirmOutbox).toHaveBeenCalledTimes(1);
    await loadImage();
    await click("确认该联系人，发送");
    expect(mocks.backend.confirmOutbox).toHaveBeenLastCalledWith("outbox-1", 5, "frame-2");
  });

  it("rejects a frame changed between display and confirmation", async () => {
    store = [awaiting()];
    await render();
    await click("查看截图并确认联系人");
    await loadImage();
    store[0] = awaiting({ version: 4, screenshot_id: "frame-2" });
    await click("确认该联系人，发送");
    expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
    expect(revokeUrl).toHaveBeenCalledWith("blob:test-1");
    expect(button("确认该联系人，发送").disabled).toBe(true);
    await loadImage();
    await click("确认该联系人，发送");
    expect(mocks.backend.confirmOutbox).toHaveBeenCalledExactlyOnceWith("outbox-1", 4, "frame-2");
  });

  it("expires screenshots at 120 seconds and offers read-only refresh without guessing a frame", async () => {
    vi.useFakeTimers();
    store = [awaiting()];
    await render();
    await click("查看截图并确认联系人");
    await loadImage();
    await tick(120_000);
    expect(container.textContent).toContain("截图已过期");
    expect(container.querySelector("img")).toBeNull();
    expect(button("确认该联系人，发送").disabled).toBe(true);
    await click("刷新任务与截图");
    expect(mocks.backend.getOutboxScreenshot).toHaveBeenCalledTimes(1);
    expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
    expect(mocks.backend.retryOutbox).not.toHaveBeenCalled();
  });

  it.each(["failed", "unknown", "filled", "observed", "running", "queued", "awaiting_confirmation", "queued_send", "navigating", "verifying", "cancelled"] as const)("renders %s with correct cancel/retry gates", async (status) => {
    store = [outboxTask({ status })];
    await render();
    expect(Boolean(button("取消任务"))).toBe(["queued", "awaiting_confirmation", "queued_send"].includes(status));
    expect(Boolean(button("重试并重新截图"))).toBe(status === "failed");
    if (status === "observed") {
      expect(container.textContent).toContain("本地发现匹配消息");
      expect(container.textContent).not.toContain("发送成功");
    }
    if (status === "filled") expect(container.textContent).toContain("已填入（未发送）");
    if (status === "unknown") expect(container.textContent).toContain("结果未知");
    if (status === "failed") {
      await click("重试并重新截图");
      expect(mocks.backend.retryOutbox).not.toHaveBeenCalled();
      await click("重新搜索并截图");
      expect(mocks.backend.retryOutbox).toHaveBeenCalledExactlyOnceWith("outbox-1", 1);
      expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
    }
  });

  it("never retries a failed task that may have sent, and allows cancellation with a disconnected client", async () => {
    store = [outboxTask({ status: "failed", may_have_sent: true }), outboxTask({ id: "cancel-me", status: "queued_send" })];
    mocks.backend.getConnection.mockResolvedValue(connectionSnapshot);
    await render();
    expect(button("重试并重新截图")).toBeUndefined();
    expect(button("发送入口").disabled).toBe(true);
    expect(button("取消任务").disabled).toBe(false);
    await click("取消任务");
    expect(mocks.backend.cancelOutbox).toHaveBeenCalledExactlyOnceWith("cancel-me", 1);
  });

  it("preserves the panel while account observation is blocked and revokes the screenshot", async () => {
    store = [awaiting({ action: "test" })];
    await render();
    await click("查看截图并确认联系人");
    await loadImage();
    expect(button("确认该联系人，仅填入").disabled).toBe(false);
    await act(async () => accountSession.suspend());
    expect(container.textContent).toContain("等待截图确认");
    expect(container.querySelector("img")).toBeNull();
    expect(revokeUrl).toHaveBeenCalledWith("blob:test-1");
    expect(button("取消任务").disabled).toBe(true);
    expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
  });

  it("ignores a late old-account screenshot after switching accounts", async () => {
    store = [awaiting()];
    let resolve!: (blob: Blob) => void;
    mocks.backend.getOutboxScreenshot.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    await render();
    await click("查看截图并确认联系人");
    store = [];
    await act(async () => accountSession.accept({ ...readySnapshot(), account: { ...connectionSnapshot.account, self_ali_id: "seller-b", epoch: "b" } }));
    await act(async () => resolve(screenshotPng()));
    expect(createUrl).not.toHaveBeenCalled();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).not.toContain("buyer-login · 第");
    expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
  });

  it("keeps failed image decoding unconfirmable", async () => {
    store = [awaiting()];
    await render();
    await click("查看截图并确认联系人");
    await act(async () => container.querySelector("img")!.dispatchEvent(new Event("error")));
    expect(button("确认该联系人，发送").disabled).toBe(true);
    expect(container.textContent).toContain("截图显示失败");
    await click("稍后确认");
    expect(revokeUrl).toHaveBeenCalledWith("blob:test-1");
  });

  it("does not roll a cancelled task back when an older poll arrives late", async () => {
    store = [outboxTask()];
    await render();
    const older = structuredClone(store);
    let resolve!: (tasks: OutboxTask[]) => void;
    mocks.backend.listOutbox.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    await click("刷新任务");
    await click("取消任务");
    expect(container.textContent).toContain("已取消");
    await act(async () => resolve(older));
    expect(container.textContent).toContain("已取消");
    expect(button("取消任务")).toBeUndefined();
  });

  it.each(["seller", "directory"])("isolates pending intents by %s and recovers them when returning", async (scope) => {
    mocks.backend.sendMessage.mockRejectedValue(new Error("timeout"));
    await render();
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    const original = mocks.backend.sendMessage.mock.calls[0][0];
    const second = { ...readySnapshot(), account: { ...connectionSnapshot.account, epoch: "b", ...(scope === "seller" ? { self_ali_id: "seller-b" } : { data_dir: "D:/other-data" }) } };
    await act(async () => accountSession.accept(second));
    expect(button("恢复原提交")).toBeUndefined();
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage.mock.calls[1][0].idempotency_key).not.toBe(original.idempotency_key);
    await act(async () => accountSession.accept({ ...readySnapshot(), account: { ...connectionSnapshot.account, epoch: "a-returned" } }));
    await click("恢复原提交");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage.mock.calls[2][0]).toEqual(original);
  });

  it.each([
    ["failed", true], ["failed", false], ["unknown", false],
  ] as const)("keeps one new-send key after a lost %s response (listed=%s)", async (status, listed) => {
    store = [outboxTask({ status: "observed", created_at: 1 })];
    const uuid = vi.spyOn(crypto, "randomUUID");
    await render();
    await click("新建发送");
    expect(uuid).toHaveBeenCalledTimes(1);
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
    const key = uuid.mock.results[0].value;
    mocks.backend.listOutbox.mockImplementation(async () => {
      expect(JSON.parse(localStorage.getItem(intentKey(connectionSnapshot.account, "42"))!)).toContainEqual(expect.objectContaining({ key }));
      return structuredClone(listed ? store : [store[0]]);
    });
    mocks.backend.sendMessage.mockImplementationOnce(async (input: SendMessageInput) => {
      store.push(outboxTask({ id: "task-K2", idempotency_key: input.idempotency_key, status, reason: status === "failed" ? "queue_full" : null, created_at: 2 }));
      throw new Error("response lost");
    });
    await click("确认收件人，开始搜索（发送）");
    expect(container.querySelector('[aria-label="新建发送：再次确认"]')).not.toBeNull();
    expect(mocks.backend.sendMessage.mock.calls[0][0].idempotency_key).toBe(key);
    // Re-rendering and repeated confirmation cannot allocate K3, even if K2 is terminal.
    await render({ draft: "later draft" });
    mocks.backend.sendMessage.mockImplementationOnce(async (input: SendMessageInput) => {
      expect(input.idempotency_key).toBe(key);
      return { outbox: structuredClone(store[1]) };
    });
    await click("确认收件人，开始搜索（发送）");
    expect(uuid).toHaveBeenCalledTimes(1);
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(listed ? 1 : 2);
    expect(store).toHaveLength(2);
    expect(container.querySelector('[aria-label="任务 task-K2"]')?.textContent).toContain(status === "failed" ? "任务失败" : "结果未知");
    expect(container.querySelector('[aria-label="新建发送：再次确认"]')).toBeNull();
    expect(JSON.parse(localStorage.getItem(intentKey(connectionSnapshot.account, "42"))!)).toContainEqual({ key, content: "submitted text", action: "send", taskId: "task-K2" });
    expect(container.querySelector("textarea")?.value).toBe("later draft");
  });

  it("persists the new dialog key before lookup and retains it when lookup fails", async () => {
    store = [outboxTask({ status: "observed" })];
    const uuid = vi.spyOn(crypto, "randomUUID");
    await render();
    await click("新建发送");
    const key = uuid.mock.results[0].value;
    mocks.backend.listOutbox.mockImplementationOnce(async () => {
      expect(JSON.parse(localStorage.getItem(intentKey(connectionSnapshot.account, "42"))!)[0].key).toBe(key);
      throw new Error("lookup timed out");
    });
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage.mock.calls[0][0].idempotency_key).toBe(key);
    expect(uuid).toHaveBeenCalledTimes(1);
  });

  it("allocates another new-send key only after closing and deliberately reopening the dialog", async () => {
    store = [outboxTask({ status: "observed" })];
    const uuid = vi.spyOn(crypto, "randomUUID");
    await render();
    await click("新建发送");
    const first = uuid.mock.results[0].value;
    await render({ draft: "unrelated edit" });
    expect(uuid).toHaveBeenCalledTimes(1);
    await click("返回编辑");
    await click("新建发送");
    const second = uuid.mock.results[1].value;
    expect(second).not.toBe(first);
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage.mock.calls[0][0].idempotency_key).toBe(second);
    expect(uuid).toHaveBeenCalledTimes(2);
  });

  it("preserves a busy-409 intent without inventing a failed task or generating a replacement key", async () => {
    store = [outboxTask({ status: "observed" })];
    await render();
    await click("新建发送");
    mocks.backend.sendMessage.mockRejectedValueOnce(new ApiError("客户端正在执行其他任务", "/api/conversations/42/messages", { status: 409 }));
    await click("确认收件人，开始搜索（发送）");
    const input = mocks.backend.sendMessage.mock.calls[0][0];
    expect(container.textContent).toContain("提交冲突（409）");
    expect(container.textContent).toContain("客户端正在执行其他任务");
    expect(container.textContent).not.toContain("任务失败");
    expect(container.querySelectorAll("article")).toHaveLength(1);
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage.mock.calls[1][0]).toEqual(input);
  });

  it("keeps an old retried task reachable beyond the newest 100 records and polls it to screenshot confirmation", async () => {
    vi.useFakeTimers();
    store = [outboxTask({ status: "failed", created_at: 1 })];
    mocks.backend.listOutbox.mockImplementation(async () => recentTasks());
    await render();
    addRecentTasks();
    await click("重试并重新截图");
    await click("重新搜索并截图");
    expect(mocks.backend.retryOutbox).toHaveBeenCalledExactlyOnceWith("outbox-1", 1);
    expect(recentTasks().some((task) => task.id === "outbox-1")).toBe(false);
    expect(container.querySelector('[aria-label="任务 outbox-1"]')?.textContent).toContain("等待搜索");
    expect(JSON.parse(localStorage.getItem(intentKey(connectionSnapshot.account, "42"))!)).toContainEqual({ key: "intent-1", content: "submitted text", action: "send", taskId: "outbox-1" });
    store[0] = awaiting({ created_at: 1 });
    await tick();
    expect(mocks.backend.getOutbox).toHaveBeenCalledWith("outbox-1");
    expect(container.querySelector('[aria-label="任务 outbox-1"]')?.textContent).toContain("等待截图确认");
    await click("查看截图并确认联系人");
    expect(mocks.backend.getOutboxScreenshot).toHaveBeenCalledExactlyOnceWith("outbox-1", "frame-1", 3);
    expect(button("确认该联系人，发送").disabled).toBe(true);
    expect(mocks.backend.confirmOutbox).not.toHaveBeenCalled();
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  });

  it("recovers a persisted task ID on reload even when the recent list omits it", async () => {
    mocks.backend.listOutbox.mockImplementation(async () => recentTasks());
    mocks.backend.sendMessage.mockImplementationOnce(async (input: SendMessageInput) => {
      const task = outboxTask({ id: "old-submission", idempotency_key: input.idempotency_key, created_at: 1 });
      store.push(task);
      return { outbox: task };
    });
    await render();
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    const key = mocks.backend.sendMessage.mock.calls[0][0].idempotency_key;
    expect(JSON.parse(localStorage.getItem(intentKey(connectionSnapshot.account, "42"))!)).toEqual([{ key, content: "submitted text", action: "send", taskId: "old-submission" }]);
    addRecentTasks();
    store[0] = awaiting({ id: "old-submission", idempotency_key: key, created_at: 1 });
    await act(async () => root.unmount());
    root = createRoot(container);
    await render();
    expect(mocks.backend.getOutbox).toHaveBeenCalledWith("old-submission");
    expect(container.querySelector('[aria-label="任务 old-submission"]')?.textContent).toContain("等待截图确认");
    expect(button("恢复原提交")).toBeUndefined();
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
    await click("发送入口");
    await click("确认收件人，开始搜索（发送）");
    expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("retries a failed supplement read without dropping the known task or POSTing", async () => {
    vi.useFakeTimers();
    store = [outboxTask({ created_at: 1 })];
    mocks.backend.listOutbox.mockImplementation(async () => recentTasks());
    await render();
    addRecentTasks();
    mocks.backend.getOutbox.mockRejectedValueOnce(new Error("read timeout"));
    await tick();
    expect(container.textContent).toContain("任务读取失败");
    expect(container.querySelector('[aria-label="任务 outbox-1"]')?.textContent).toContain("等待搜索");
    store[0] = awaiting({ created_at: 1 });
    await tick();
    expect(container.querySelector('[aria-label="任务 outbox-1"]')?.textContent).toContain("等待截图确认");
    expect(mocks.backend.getOutbox).toHaveBeenCalledTimes(2);
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
    expect(mocks.backend.retryOutbox).not.toHaveBeenCalled();
  });

  it("discards a delayed supplement after switching account scope", async () => {
    const storageKey = intentKey(connectionSnapshot.account, "42");
    const record = JSON.stringify([{ key: "intent-1", content: "submitted text", action: "send", taskId: "outbox-1" }]);
    localStorage.setItem(storageKey, record);
    let resolve!: (task: OutboxTask) => void;
    mocks.backend.getOutbox.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    await render();
    expect(mocks.backend.getOutbox).toHaveBeenCalledExactlyOnceWith("outbox-1");
    const nextAccount = { ...connectionSnapshot.account, self_ali_id: "seller-b", epoch: "b" };
    await act(async () => accountSession.accept({ ...readySnapshot(), account: nextAccount }));
    await act(async () => resolve(awaiting()));
    expect(container.querySelector('[aria-label="任务 outbox-1"]')).toBeNull();
    expect(localStorage.getItem(intentKey(nextAccount, "42"))).toBeNull();
    expect(localStorage.getItem(storageKey)).toBe(record);
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  });

  it("merges a late direct read by version without rolling back a cancelled task", async () => {
    vi.useFakeTimers();
    store = [outboxTask({ created_at: 1 })];
    mocks.backend.listOutbox.mockImplementation(async () => recentTasks());
    await render();
    const older = structuredClone(store[0]);
    addRecentTasks();
    let resolve!: (task: OutboxTask) => void;
    mocks.backend.getOutbox.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    await tick();
    await click("取消任务");
    expect(container.querySelector('[aria-label="任务 outbox-1"]')?.textContent).toContain("已取消");
    await act(async () => resolve(older));
    expect(container.querySelector('[aria-label="任务 outbox-1"]')?.textContent).toContain("已取消");
    expect(button("取消任务")).toBeUndefined();
  });

  it.each([
    { id: "wrong-id" }, { conversation_id: 43 }, { idempotency_key: "wrong-key" },
  ])("rejects a supplement whose identity mismatches %j", async (mismatch) => {
    localStorage.setItem(intentKey(connectionSnapshot.account, "42"), JSON.stringify([{ key: "intent-1", content: "submitted text", action: "send", taskId: "outbox-1" }]));
    mocks.backend.getOutbox.mockResolvedValue(awaiting(mismatch));
    await render();
    expect(container.textContent).toContain("任务读取失败");
    expect(container.querySelectorAll("article")).toHaveLength(0);
    expect(container.querySelector("img")).toBeNull();
    expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  });
});
