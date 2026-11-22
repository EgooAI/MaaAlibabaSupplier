// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccountProvider } from "@/features/account/AccountProvider";
import { CustomerStageControl } from "@/features/chat/conversation/CustomerStageControl";
import { CustomerInfo } from "@/features/chat/conversation/CustomerInfo";
import { PortraitEditor } from "@/features/chat/conversation/PortraitEditor";
import { accountSession } from "@/services/accountSession";
import { adaptConversationDetail } from "@/services/chatAdapter";
import { ApiError, PortraitTagConflict } from "@/services/httpAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import { authenticatedSession } from "@/test/support/authFixture";
import { PORTRAIT_TAG_NAME_MAX } from "@/domain/contract.generated";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { PortraitTag } from "@/types/portrait";

const mocks = vi.hoisted(() => ({
  message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
  backend: { getConnection: vi.fn(), savePortraitBatch: vi.fn(), listPortraitOperations: vi.fn(), createPortraitTag: vi.fn() },
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
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const onSaved = vi.fn();
const onTagCreated = vi.fn();
const tag = (tagId: string, name: string, archived = false): PortraitTag => ({
  tag_id: tagId, name, archived, description: "", version: 1, created_at: 0, updated_at: 0, usage_count: 0,
});
const renderTags = async (conversation = detail(), tags: PortraitTag[] = []) => {
  await act(async () => root.render(<AccountProvider><PortraitEditor conversation={conversation} tags={tags} onSaved={onSaved} onTagCreated={onTagCreated} /></AccountProvider>));
};
const renderInfo = async (conversation: ConversationDetail) => {
  await act(async () => root.render(<AccountProvider><CustomerInfo conversation={conversation} open onClose={() => {}} portraitTags={[]} /></AccountProvider>));
};
const tagInput = () => container.querySelector<HTMLInputElement>('input[aria-label="画像标签"]')!;
const selectedTagNames = () => [...container.querySelectorAll(".ant-select-selection-item-content")].map((node) => node.textContent);

async function enterTag(name: string) {
  const input = tagInput();
  await act(async () => {
    input.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    input.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, name);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true, cancelable: true })));
  await act(async () => input.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true, cancelable: true })));
}

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
  if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else Reflect.deleteProperty(navigator, "clipboard");
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("offers copying on every identity field and copies its full value", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const conversation = detail();
  const values = { aliId: "buyer-alibaba", memberId: "member-42", loginId: "buyer@example.com", encryptAccountId: "encrypted-".repeat(40) };
  Object.assign(conversation.customer, values);
  await renderInfo(conversation);
  expect(document.body.textContent).toContain("身份标识");
  for (const [index, label] of ["Ali ID", "会员 ID", "登录 ID", "加密 ID"].entries()) {
    const field = document.querySelector<HTMLButtonElement>(`button[aria-label="复制${label}"]`)!;
    await act(async () => field.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    await act(async () => { await vi.advanceTimersByTimeAsync(150); });
    expect(document.querySelector('[role="tooltip"]')?.textContent).toBe("单击以复制");
    await act(async () => field.click());
    expect(writeText).toHaveBeenNthCalledWith(index + 1, Object.values(values)[index]);
    await act(async () => field.dispatchEvent(new MouseEvent("mouseout", { bubbles: true })));
    await act(async () => { await vi.advanceTimersByTimeAsync(150); });
  }
  expect(mocks.message.success).toHaveBeenCalledTimes(4);
});

it("leaves missing identifiers uncopyable and reports clipboard failures without claiming success", async () => {
  const writeText = vi.fn().mockRejectedValue(new Error("Clipboard denied"));
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const conversation = detail();
  Object.assign(conversation.customer, { aliId: "buyer-alibaba", memberId: undefined, loginId: "", encryptAccountId: "  " });
  await renderInfo(conversation);
  for (const label of ["会员 ID", "登录 ID", "加密 ID"]) {
    const field = document.querySelector<HTMLButtonElement>(`button[aria-label="复制${label}"]`)!;
    expect(field.disabled).toBe(true);
    expect(field.textContent).toContain("—");
    await act(async () => field.click());
  }
  expect(writeText).not.toHaveBeenCalled();
  await act(async () => document.querySelector<HTMLButtonElement>('button[aria-label="复制Ali ID"]')!.click());
  expect(mocks.message.error).toHaveBeenCalledWith("复制失败，请手动复制");
  expect(mocks.message.success).not.toHaveBeenCalled();
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
  await renderTags(conversation, [tag("new", "高潜")]);
  await choose("画像标签", "高潜");
  await act(async () => button("保存标签").click());
  expect(mocks.backend.savePortraitBatch).toHaveBeenCalledExactlyOnceWith({
    request_id: expect.any(String),
    targets: [{ sid: 42, expected_cid: 1001, expected_version: 3 }],
    changes: { add_tag_ids: ["new"] },
  });
  expect(onSaved).toHaveBeenCalledOnce();
});

it("creates an entered tag before selecting its ID and saves additions and removals only on request", async () => {
  let finish!: (value: { tag: PortraitTag }) => void;
  mocks.backend.createPortraitTag.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  await renderTags();
  await enterTag("  新客户  ");
  expect(mocks.backend.createPortraitTag).toHaveBeenCalledExactlyOnceWith({ name: "新客户" });
  expect(tagInput().disabled).toBe(true);
  expect(button("保存标签").disabled).toBe(true);
  expect(mocks.backend.savePortraitBatch).not.toHaveBeenCalled();
  expect(selectedTagNames()).toEqual(["长期合作"]);

  const created = tag("created-id", "新客户");
  await act(async () => finish({ tag: created }));
  expect(onTagCreated).toHaveBeenCalledExactlyOnceWith(created);
  expect(selectedTagNames()).toEqual(["长期合作", "新客户"]);
  expect(tagInput().value).toBe("");
  mocks.backend.createPortraitTag.mockRejectedValue(new PortraitTagConflict(created, new ApiError("同名标签已存在", "/api/portraits/tags", { status: 409 })));
  await enterTag(" 新客户 ");
  expect(mocks.backend.createPortraitTag).toHaveBeenCalledTimes(2);
  expect(selectedTagNames()).toEqual(["长期合作", "新客户"]);
  await act(async () => container.querySelector<HTMLElement>('.ant-select-selection-item[title="长期合作"] .ant-select-selection-item-remove')!.click());
  expect(selectedTagNames()).toEqual(["新客户"]);
  expect(mocks.backend.savePortraitBatch).not.toHaveBeenCalled();
  await act(async () => button("保存标签").click());
  expect(mocks.backend.savePortraitBatch).toHaveBeenCalledExactlyOnceWith({
    request_id: expect.any(String),
    targets: [{ sid: 42, expected_cid: 1001, expected_version: 3 }],
    changes: { add_tag_ids: ["created-id"], remove_tag_ids: ["existing"] },
  });
});

it("shares created tags with the manager and replaces them with parent catalog updates", async () => {
  const conversation = detail();
  const created = tag("created-id", "新客户");
  mocks.backend.createPortraitTag.mockResolvedValue({ tag: created });
  await act(async () => root.render(<AccountProvider><PortraitEditor conversation={conversation} tags={[]} /></AccountProvider>));
  await enterTag("新客户");
  expect(selectedTagNames()).toEqual(["长期合作", "新客户"]);
  await act(async () => button("管理标签").click());
  const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
  expect(dialog.textContent).toContain("新客户");
  expect(dialog.textContent).not.toContain("长期合作");

  const updated = { ...created, name: "重点客户", description: "父级更新说明", archived: true, usage_count: 7, version: 2 };
  await act(async () => root.render(<AccountProvider><PortraitEditor conversation={conversation} tags={[updated]} /></AccountProvider>));
  expect(selectedTagNames()).toEqual(["长期合作", "重点客户（已停用）"]);
  expect(dialog.textContent).toContain("重点客户");
  expect(dialog.textContent).toContain("父级更新说明");
  expect(dialog.textContent).toContain("7 位客户关联");
  expect(dialog.textContent).not.toContain("新客户");
  expect([...dialog.querySelectorAll("button")].filter((node) => node.textContent?.replace(/\s/g, "") === "编辑")).toHaveLength(1);
  expect(mocks.backend.savePortraitBatch).not.toHaveBeenCalled();
});

it("keeps a late duplicate receipt from selecting a tag archived by a newer catalog", async () => {
  const original = tag("shared-id", "Straße");
  let fail!: (error: unknown) => void;
  mocks.backend.createPortraitTag.mockReturnValue(new Promise((_resolve, reject) => { fail = reject; }));
  await renderTags(detail(), [original]);
  await enterTag("STRASSE");
  const updated = { ...original, archived: true, version: 2 };
  await renderTags(detail(), [updated]);
  await act(async () => fail(new PortraitTagConflict(original, new ApiError("同名标签已存在", "/api/portraits/tags", { status: 409 }))));
  expect(selectedTagNames()).toEqual(["长期合作"]);
  expect(onTagCreated).toHaveBeenCalledExactlyOnceWith(updated);
  expect(mocks.message.warning).toHaveBeenCalledWith("该标签已停用，不能新增关联");
  expect(mocks.message.success).not.toHaveBeenCalled();
  await act(async () => button("保存标签").click());
  expect(mocks.backend.savePortraitBatch).not.toHaveBeenCalled();
});

it("reuses normalized names and rejects archived or overlong tags entered as free text", async () => {
  const conversation = detail();
  const equivalent = tag("equivalent-id", "Straße");
  const archived = tag("archived-id", "旧标签", true);
  const conflict = new ApiError("同名标签已存在", "/api/portraits/tags", { status: 409 });
  mocks.backend.createPortraitTag.mockRejectedValueOnce(new PortraitTagConflict(equivalent, conflict))
    .mockRejectedValueOnce(new PortraitTagConflict(equivalent, conflict))
    .mockRejectedValueOnce(new PortraitTagConflict(archived, conflict));
  conversation.customer.businessTags.push({ tag_id: "archived-id", name: "旧标签", archived: true });
  await renderTags(conversation, [equivalent, archived]);
  expect(selectedTagNames()).toEqual(["长期合作", "旧标签（已停用）"]);
  await act(async () => container.querySelector<HTMLElement>('.ant-select-selection-item[title="旧标签（已停用）"] .ant-select-selection-item-remove')!.click());
  await enterTag(" ＳＴＲＡＳＳＥ ");
  await enterTag(" strasse ");
  await enterTag("旧标签");
  expect(mocks.message.warning).toHaveBeenCalledWith("该标签已停用，不能新增关联");
  await enterTag("长".repeat(PORTRAIT_TAG_NAME_MAX + 1));
  expect(mocks.message.warning).toHaveBeenCalledWith(`标签名称不能超过 ${PORTRAIT_TAG_NAME_MAX} 个字符`);
  expect(mocks.backend.createPortraitTag.mock.calls.map(([input]) => input)).toEqual([{ name: "STRASSE" }, { name: "strasse" }, { name: "旧标签" }]);
  expect(selectedTagNames()).toEqual(["长期合作", "Straße"]);
  await act(async () => button("保存标签").click());
  expect(mocks.backend.savePortraitBatch.mock.calls[0][0].changes).toEqual({ add_tag_ids: ["equivalent-id"], remove_tag_ids: ["archived-id"] });
});

it("keeps a failed creation out of the selection and preserves the name for retry", async () => {
  mocks.backend.createPortraitTag.mockRejectedValueOnce(new ApiError("标签创建失败", "/api/portraits/tags", { status: 409 }))
    .mockResolvedValueOnce({ tag: tag("retry-id", "待重试") });
  await renderTags();
  await enterTag("待重试");
  expect(mocks.message.error).toHaveBeenCalledWith("标签创建失败");
  expect(selectedTagNames()).toEqual(["长期合作"]);
  expect(tagInput().value).toBe("待重试");
  expect(tagInput().disabled).toBe(false);
  expect(onTagCreated).not.toHaveBeenCalled();
  await enterTag("待重试");
  expect(selectedTagNames()).toEqual(["长期合作", "待重试"]);
  await act(async () => button("保存标签").click());
  expect(mocks.backend.savePortraitBatch.mock.calls[0][0].changes).toEqual({ add_tag_ids: ["retry-id"] });
});

it.each(["conversation", "version", "account"])("discards a late tag creation after changing the %s", async (change) => {
  let finish!: (value: { tag: PortraitTag }) => void;
  mocks.backend.createPortraitTag.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  await renderTags();
  await enterTag("延迟标签");
  expect(mocks.backend.createPortraitTag).toHaveBeenCalledOnce();
  if (change === "conversation") {
    await renderTags(detail("43", 1002));
  } else if (change === "version") {
    const updated = detail();
    updated.customer.businessVersion += 1;
    await renderTags(updated);
  } else {
    const next = structuredClone(accountSession.get().snapshot!);
    next.account.epoch = "other-account";
    next.account.self_ali_id = "seller-b";
    await act(async () => accountSession.accept(next));
  }
  await act(async () => finish({ tag: tag("late-id", "延迟标签") }));
  expect(selectedTagNames()).toEqual(["长期合作"]);
  expect(onTagCreated).not.toHaveBeenCalled();
  expect(mocks.message.success).not.toHaveBeenCalled();
  expect(mocks.message.error).not.toHaveBeenCalled();
  expect(mocks.backend.savePortraitBatch).not.toHaveBeenCalled();
});
