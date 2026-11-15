// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountProvider } from "@/features/account/AccountProvider";
import { CustomerStageControl } from "@/features/chat/conversation/CustomerStageControl";
import { PortraitEditor } from "@/features/chat/conversation/PortraitEditor";
import { accountSession } from "@/services/accountSession";
import { adaptConversationDetail } from "@/services/chatAdapter";
import { ApiError } from "@/services/httpAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import { authenticatedSession } from "@/test/support/authFixture";
import type { ConversationDetail } from "@/types/chatCanonical";

const mocks = vi.hoisted(() => ({
  message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
  backend: { getConnection: vi.fn(), savePortraitBatch: vi.fn(), listPortraitOperations: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", async (importOriginal) => ({
  ...await importOriginal<typeof import("antd")>(),
  App: { useApp: () => ({ message: mocks.message }) },
}));

const detail = (id = "42", cid = 1001): ConversationDetail => {
  const base = adaptConversationDetail({
    sid: Number(id), name: `buyer-${id}`, participants: [], messages: [], latest: { content: "", updated_at: null },
    unread_count: 0, reply_state: "none", pending_since: null, history_pending: false, uncertain: false,
  });
  return { ...base, customer: {
    ...base.customer, cid, businessStage: "following_up", businessVersion: 3, portraitEditable: true,
    businessTags: [{ tag_id: "existing", name: "长期合作" }],
  } };
};

let root: Root;
let container: HTMLDivElement;
const onSaved = vi.fn();
const renderStage = async (conversation = detail()) => {
  await act(async () => root.render(<AccountProvider><CustomerStageControl conversation={conversation} onSaved={onSaved} /></AccountProvider>));
};
const stageButton = () => container.querySelector<HTMLButtonElement>('button[aria-label^="客户阶段："]')!;
const button = (text: string) => [...document.querySelectorAll<HTMLButtonElement>("button")]
  .find((node) => node.textContent?.replace(/\s/g, "") === text)!;

async function choose(label: string, optionLabel: string) {
  const input = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  expect(input).not.toBeNull();
  await act(async () => {
    input.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    input.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  const option = [...document.querySelectorAll<HTMLElement>(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option")]
    .find((node) => node.textContent?.trim() === optionLabel)!;
  expect(option).toBeDefined();
  await act(async () => option.click());
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  const snapshot = structuredClone(connectionSnapshot);
  snapshot.client.connected = true;
  accountSession.accept(snapshot);
  mocks.backend.getConnection.mockResolvedValue(snapshot);
  mocks.backend.listPortraitOperations.mockResolvedValue({ operations: [] });
  mocks.backend.savePortraitBatch.mockResolvedValue({ changed: [], unchanged: [] });
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

it("saves stage and failure details with the displayed version while leaving tags untouched", async () => {
  await renderStage();
  await act(async () => stageButton().click());
  await choose("客户阶段", "流失/失败");
  await choose("失败原因", "价格");
  await act(async () => {
    const note = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="失败备注"]')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(note, "  Budget exceeded  ");
    note.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(stageButton().textContent).toContain("跟进中");
  await act(async () => button("保存阶段").click());
  expect(mocks.backend.savePortraitBatch).toHaveBeenCalledOnce();
  expect(mocks.backend.savePortraitBatch.mock.calls[0][0]).toEqual({
    request_id: expect.any(String),
    targets: [{ sid: 42, expected_cid: 1001, expected_version: 3 }],
    changes: { stage: "lost", lost_reason: "price", lost_note: "Budget exceeded" },
  });
  expect(onSaved).toHaveBeenCalledOnce();

  const updated = detail();
  Object.assign(updated.customer, { businessStage: "lost", lostReason: "price", lostNote: "Budget exceeded", businessVersion: 4 });
  await renderStage(updated);
  expect(stageButton().textContent).toContain("流失/失败");
  await act(async () => stageButton().click());
  await choose("客户阶段", "已成交");
  expect(document.querySelector('textarea[aria-label="失败备注"]')).toBeNull();
  await act(async () => button("保存阶段").click());
  expect(mocks.backend.savePortraitBatch.mock.calls[1][0]).toEqual({
    request_id: expect.any(String),
    targets: [{ sid: 42, expected_cid: 1001, expected_version: 4 }],
    changes: { stage: "won" },
  });
});

it("discards a cancelled stage draft and keeps the saved stage visible", async () => {
  await renderStage();
  await act(async () => stageButton().click());
  await choose("客户阶段", "已成交");
  await act(async () => button("取消").click());
  await act(async () => stageButton().click());
  expect(stageButton().textContent).toContain("跟进中");
  expect(button("保存阶段").disabled).toBe(true);
  expect(mocks.backend.savePortraitBatch).not.toHaveBeenCalled();
});

it.each(["conversation", "account"])("discards a late stage receipt after changing the %s", async (change) => {
  let finish!: (value: unknown) => void;
  mocks.backend.savePortraitBatch.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  await renderStage();
  await act(async () => stageButton().click());
  await choose("客户阶段", "已成交");
  await act(async () => button("保存阶段").click());
  if (change === "conversation") {
    await renderStage(detail("43", 1002));
  } else {
    const next = structuredClone(accountSession.get().snapshot!);
    next.account.epoch = "other-account";
    next.account.self_ali_id = "seller-b";
    await act(async () => accountSession.accept(next));
  }
  await act(async () => finish({ changed: [], unchanged: [] }));
  expect(onSaved).not.toHaveBeenCalled();
  expect(mocks.message.success).not.toHaveBeenCalled();
  expect(stageButton().textContent).toContain("跟进中");
  expect(mocks.backend.savePortraitBatch).toHaveBeenCalledOnce();
});

it("refreshes a conflicting stage version without reporting success or resubmitting", async () => {
  mocks.backend.savePortraitBatch.mockRejectedValue(new ApiError("画像目标已变化", "/api/portraits/batch", { status: 409 }));
  await renderStage();
  await act(async () => stageButton().click());
  await choose("客户阶段", "已成交");
  await act(async () => button("保存阶段").click());
  expect(onSaved).toHaveBeenCalledOnce();
  expect(mocks.message.error).toHaveBeenCalledWith("画像目标已变化");
  expect(mocks.message.success).not.toHaveBeenCalled();
  expect(stageButton().textContent).toContain("跟进中");
  expect(mocks.backend.savePortraitBatch).toHaveBeenCalledOnce();
});

it("saves only tag changes even when the customer has incomplete failure details", async () => {
  const conversation = detail();
  conversation.customer.businessStage = "lost";
  conversation.customer.lostReason = null;
  conversation.customer.lostNote = " original note ";
  await act(async () => root.render(<AccountProvider><PortraitEditor conversation={conversation} onSaved={onSaved} tags={[
    { tag_id: "new", name: "高潜", archived: false, description: "", version: 1, created_at: 0, updated_at: 0, usage_count: 0 },
  ]} /></AccountProvider>));
  await choose("画像标签", "高潜");
  await act(async () => button("保存标签").click());
  expect(mocks.backend.savePortraitBatch).toHaveBeenCalledExactlyOnceWith({
    request_id: expect.any(String),
    targets: [{ sid: 42, expected_cid: 1001, expected_version: 3 }],
    changes: { add_tag_ids: ["new"] },
  });
  expect(onSaved).toHaveBeenCalledOnce();
});
