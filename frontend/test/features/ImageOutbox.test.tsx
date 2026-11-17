// @vitest-environment happy-dom
import { act, useState, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { File as NativeFile, Blob as NativeBlob } from "node:buffer";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OutboxWorkspace } from "@/features/chat/outbox/OutboxWorkspace";
import { OutboxTaskBubble } from "@/features/chat/outbox/OutboxTaskBubble";
import { useOutbox } from "@/features/chat/outbox/useOutbox";
import { loadImageDraft, saveImageDraft } from "@/features/chat/outbox/imageDraftStorage";
import { intentKey } from "@/features/chat/outbox/intentStorage";
import { accountSession, scopeBackend } from "@/services/accountSession";
import { ApiError } from "@/services/httpAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import { adaptConversationDetail } from "@/services/chatAdapter";
import type { ImageAttachment, OutboxTask, SendMessageInput } from "@/types/chatOperations";
import type { OperationsBackend } from "@/services/interfaces";
import { outboxTask } from "@/test/support/outboxFixture";

const mocks = vi.hoisted(() => ({ backend: {
  listOutbox: vi.fn(), getOutbox: vi.fn(), sendMessage: vi.fn(),
  getImageAttachment: vi.fn(), uploadImageAttachment: vi.fn(), getImageAttachmentPreview: vi.fn(),
} }));
vi.mock("@/features/account/AccountProvider", async () => {
  const { AccountChangedError } = await import("@/services/accountSession");
  return {
    AccountChangedError,
    useAccount: () => ({ snapshot: accountSession.get().snapshot, blocked: accountSession.get().blocked }),
    useAccountBackend: () => useState(() => scopeBackend(mocks.backend as unknown as OperationsBackend))[0],
  };
});
vi.mock("antd", () => {
  const Button = ({ children, onClick, disabled, loading }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button onClick={onClick} disabled={disabled || loading}>{children}</button>;
  return { Button, Image: ({ alt }: { alt: string }) => <div role="img" aria-label={alt} />,
    Modal: () => null, Popover: ({ children }: { children: ReactNode }) => <>{children}</>, Avatar: () => null };
});
vi.mock("@/features/chat/workspace/ChatComposer", () => ({ ChatComposer: ({ value, imageTools, hasAttachment, sendDisabled, loading, onSend, onFill }: {
  value: string; imageTools: ReactNode; hasAttachment: boolean; sendDisabled: boolean; loading: boolean; onSend: () => void; onFill: () => void;
}) => <>{imageTools}<textarea value={value} readOnly /><button disabled={sendDisabled || loading || (!hasAttachment && !value.trim())} onClick={onSend}>{hasAttachment ? "发送图片" : "发送"}</button><button disabled={sendDisabled || loading} onClick={onFill}>仅填入客户端</button></> }));

const detail = adaptConversationDetail({ sid: 42, name: "Buyer", participants: [], messages: [], latest: { content: "", updated_at: null }, unread_count: 0, reply_state: "waiting_customer", pending_since: null, history_pending: false, uncertain: false, read_snapshot: "snapshot" });
detail.customer.loginId = "buyer-login";
let root: Root;
let container: HTMLDivElement;
let tasks: OutboxTask[];
let attachments: Map<string, ImageAttachment>;
const draftKey = () => `${intentKey(connectionSnapshot.account, "42")}:image`;
const file = (name = "test.png") => new NativeFile([new Uint8Array([1, 2, 3])], name, { type: "image/png" }) as unknown as File;
const blob = () => new NativeBlob([new Uint8Array([1, 2, 3])], { type: "image/png" }) as unknown as Blob;
const meta = (id: string): ImageAttachment => ({ id, width: 12, height: 8, bytes: 99, mime_type: "image/png", sha256: "a".repeat(64) });

function Harness() {
  const outbox = useOutbox("42");
  const [text, setText] = useState("preserved text");
  return <>{outbox.tasks.map((task) => <OutboxTaskBubble key={task.id} task={task} outbox={outbox} />)}
    <OutboxWorkspace conversation={detail} outbox={outbox} value={text} onChange={setText}
      onOpenSuggestions={() => {}} onOpenIntentAnalysis={() => {}} onOpenStageAnalysis={() => {}}
      captureDraft={() => ({ id: "text-draft", content: text, clear: () => setText("") })} />
  </>;
}
const mount = () => act(async () => root.render(<Harness />));
const button = (label: string) => [...container.querySelectorAll("button")].find((item) => item.textContent === label)!;
const click = (label: string) => act(async () => button(label).click());
async function choose(value: File) {
  const input = container.querySelector('input[type="file"]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [value] });
  await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
}
async function ready() {
  await vi.waitFor(async () => { await act(async () => {}); expect(button("发送图片")?.disabled).toBe(false); });
}
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal("Blob", NativeBlob);
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:preview");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  localStorage.clear();
  accountSession.accept({ ...structuredClone(connectionSnapshot), capabilities: { read_chat: true, use_ai: true, operate_client: true } });
  tasks = []; attachments = new Map();
  mocks.backend.listOutbox.mockImplementation(async () => [...tasks]);
  mocks.backend.getOutbox.mockImplementation(async (id: string) => tasks.find((task) => task.id === id));
  mocks.backend.getImageAttachment.mockImplementation(async (_sid: string, id: string) => {
    if (!attachments.has(id)) throw new ApiError("missing", "attachments", { status: 404 });
    return attachments.get(id);
  });
  mocks.backend.uploadImageAttachment.mockImplementation(async (_sid: string, id: string) => { const item = meta(id); attachments.set(id, item); return item; });
  mocks.backend.getImageAttachmentPreview.mockImplementation(async () => blob());
  mocks.backend.sendMessage.mockImplementation(async (input: SendMessageInput) => {
    const task = outboxTask({ id: input.idempotency_key, idempotency_key: input.idempotency_key, conversation_id: 42,
      content: input.content, action: input.action, message_kind: input.message_kind, attachment_id: input.attachment_id,
      image: input.attachment_id ? meta(input.attachment_id) : undefined });
    tasks.push(task); return { outbox: task };
  });
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(["file", "paste"])("submits one %s image and preserves the text draft", async (entry) => {
  await mount();
  if (entry === "file") await choose(file());
  else {
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", { value: { files: [file()] } });
    await act(async () => container.querySelector("textarea")!.dispatchEvent(event));
    expect(event.defaultPrevented).toBe(true);
  }
  await ready();
  const saved = await loadImageDraft(draftKey());
  expect(saved?.attachment?.id).toBe(saved?.id);
  await click("发送图片");
  expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
  expect(mocks.backend.sendMessage.mock.calls[0][0]).toMatchObject({ content: "", message_kind: "image", attachment_id: saved!.id });
  await vi.waitFor(async () => { await act(async () => {}); expect(button("发送图片")).toBeUndefined(); });
  expect(container.querySelector("textarea")?.value).toBe("preserved text");
  expect(container.querySelector('[aria-label="发送任务图片"]')).not.toBeNull();
  expect(await loadImageDraft(draftKey())).toBeUndefined();
});

it("recovers a lost send response after reload without another send", async () => {
  await mount(); await choose(file()); await ready();
  const actual = mocks.backend.sendMessage.getMockImplementation()!;
  mocks.backend.sendMessage.mockImplementationOnce(async (...args) => { await actual(...args); throw new ApiError("timeout", "send", { kind: "timeout" }); });
  await click("发送图片");
  expect(await loadImageDraft(draftKey())).toBeDefined();
  await act(async () => root.unmount()); root = createRoot(container); await mount(); await ready();
  await click("发送图片");
  expect(mocks.backend.sendMessage).toHaveBeenCalledTimes(1);
  expect(tasks).toHaveLength(1);
});

it("ignores late upload receipts and a send receipt for a replaced image", async () => {
  const pending = deferred<ImageAttachment>();
  mocks.backend.uploadImageAttachment.mockImplementationOnce(() => pending.promise);
  await mount(); await choose(file("first.png"));
  await vi.waitFor(async () => { await act(async () => {}); expect(mocks.backend.uploadImageAttachment).toHaveBeenCalledTimes(1); });
  const firstId = mocks.backend.uploadImageAttachment.mock.calls[0][1];
  await choose(file("second.png")); await ready();
  await act(async () => pending.resolve(meta(firstId)));
  expect(container.textContent).toContain("second.png");
  const sending = deferred<{ outbox: OutboxTask }>();
  mocks.backend.sendMessage.mockImplementationOnce(() => sending.promise);
  await click("发送图片");
  const submission = mocks.backend.sendMessage.mock.calls[0][0];
  await choose(file("third.png"));
  await vi.waitFor(async () => { await act(async () => {}); expect(container.textContent).toContain("third.png"); });
  await act(async () => sending.resolve({ outbox: outboxTask({ id: submission.idempotency_key, idempotency_key: submission.idempotency_key, conversation_id: 42, content: "", message_kind: "image", attachment_id: submission.attachment_id, image: meta(submission.attachment_id) }) }));
  await ready();
  expect((await loadImageDraft(draftKey()))?.name).toBe("third.png");
  expect(container.querySelector("textarea")?.value).toBe("preserved text");
});

it("never resumes an upload under a switched account", async () => {
  const lookup = deferred<ImageAttachment>();
  mocks.backend.getImageAttachment.mockImplementationOnce(() => lookup.promise);
  await mount(); await choose(file());
  await vi.waitFor(async () => { await act(async () => {}); expect(mocks.backend.getImageAttachment).toHaveBeenCalledTimes(1); });
  accountSession.invalidate();
  await act(async () => lookup.resolve(meta(mocks.backend.getImageAttachment.mock.calls[0][1])));
  expect(mocks.backend.uploadImageAttachment).not.toHaveBeenCalled();
  expect(mocks.backend.getImageAttachmentPreview).not.toHaveBeenCalled();
  expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
});

it("rejects oversized and multiple clipboard images before upload", async () => {
  await mount();
  const oversized = file(); Object.defineProperty(oversized, "size", { value: 10_000_001 });
  await choose(oversized);
  expect(container.textContent).toContain("10,000,000");
  const event = new Event("paste", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "clipboardData", { value: { files: [file(), file()] } });
  await act(async () => container.querySelector("textarea")!.dispatchEvent(event));
  expect(container.textContent).toContain("每次只能添加一张图片");
  expect(mocks.backend.uploadImageAttachment).not.toHaveBeenCalled();
});

it("commits image drafts before reporting success and compares revisions when removing", async () => {
  const original = { id: "a".repeat(32), file: file(), name: "first" };
  const next = { ...original, id: "b".repeat(32), name: "second" };
  await saveImageDraft(draftKey(), original);
  expect(await (await loadImageDraft(draftKey()))!.file.arrayBuffer()).toEqual(await original.file.arrayBuffer());
  await saveImageDraft(draftKey(), next);
  expect(await saveImageDraft(draftKey(), undefined, original.id)).toBe(false);
  expect((await loadImageDraft(draftKey()))?.id).toBe(next.id);
  expect(await saveImageDraft(draftKey(), { ...original, attachment: meta(original.id) }, original.id)).toBe(false);
  expect((await loadImageDraft(draftKey()))?.id).toBe(next.id);
});

it("blocks upload and send when durable image storage is unavailable", async () => {
  await mount();
  vi.stubGlobal("indexedDB", undefined);
  await choose(file());
  expect(container.textContent).toContain("不支持图片草稿存储");
  expect(mocks.backend.uploadImageAttachment).not.toHaveBeenCalled();
  expect(mocks.backend.sendMessage).not.toHaveBeenCalled();
  expect(container.querySelector("textarea")?.value).toBe("preserved text");
});
