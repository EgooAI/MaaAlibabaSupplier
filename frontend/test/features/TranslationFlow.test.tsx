// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { useChatWorkbench } from "@/features/chat/hooks/useChatWorkbench";
import { accountSession } from "@/services/accountSession";
import { adaptConversationDetail, adaptConversationSummary } from "@/services/chatAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import type { ConversationAggregateDto } from "@/types/chatTransport";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { ConversationPage } from "@/types/inbox";
import { authenticatedSession } from "@/test/support/authFixture";

const mocks = vi.hoisted(() => ({
  message: { warning: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn() },
  backend: { getConnection: vi.fn(), getConversationRevision: vi.fn(), listConversations: vi.fn(), getConversation: vi.fn(), markConversationRead: vi.fn(), requestTranslations: vi.fn(), queryTranslations: vi.fn(), getTranslationJob: vi.fn(), analyzeConversation: vi.fn() },
}));
vi.mock("antd", () => ({
  App: { useApp: () => ({ message: mocks.message }) },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));

const aggregate: ConversationAggregateDto = {
  sid: 42, name: "Buyer", participants: [], messages: [],
  latest: { content: "latest", updated_at: "2026-09-08 10:00" },
  unread_count: 0, reply_state: "waiting_customer", pending_since: null, due_at: null, is_overdue: false, history_pending: false, uncertain: false, read_snapshot: "snapshot-42",
};
const pageOf = (): ConversationPage => ({ items: [adaptConversationSummary(aggregate)], total: 1, offset: 0, limit: 50, inbox_revision: 1, pagination_revision: "page-1" });
const revisionOf = { ready: true, revision: connectionSnapshot.source.revision, inbox_revision: 1, next_due_at: null };

let root: Root;
let container: HTMLDivElement;
let workbench: ReturnType<typeof useChatWorkbench>;

function Workspace() {
  const current = useChatWorkbench();
  useEffect(() => { workbench = current; }, [current]);
  return <textarea value={current.draft} readOnly />;
}
function ReadableWorkspace() {
  const account = useAccount();
  if ((account.blocked && !account.suspended) || !account.snapshot?.capabilities.read_chat) return null;
  return <Workspace key={`${account.snapshot.account.epoch}:${account.generation}`} />;
}

const detailWith = (messages: ConversationDetail["messages"]): ConversationDetail => {
  const detail = adaptConversationDetail(aggregate);
  return { ...detail, messages };
};

beforeEach(async () => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  mocks.backend.getConnection.mockReset().mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.listConversations.mockReset().mockResolvedValue(pageOf());
  mocks.backend.getConversation.mockReset().mockResolvedValue(detailWith([]));
  mocks.backend.getConversationRevision.mockReset().mockResolvedValue(revisionOf);
  mocks.backend.requestTranslations.mockReset().mockResolvedValue({ task_id: "", status: "succeeded", message: "没有需要翻译的内容" });
  mocks.backend.queryTranslations.mockReset().mockResolvedValue({ translations: {} });
  mocks.backend.getTranslationJob.mockReset().mockResolvedValue(null);
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

async function mount() {
  await act(async () => root.render(<AccountProvider><ReadableWorkspace /></AccountProvider>));
}

describe("translation flow", () => {
  it("absorbs cached translations for every party and skips card bubbles", async () => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([
      { id: "one", role: "buyer", content: "hello", createdAt: "now" },
      { id: "two", role: "buyer", content: "cached", createdAt: "now" },
      { id: "three", role: "seller", content: "seller text", createdAt: "now" },
      { id: "four", role: "system", content: "auto reply", createdAt: "now" },
      { id: "five", role: "card", content: "[卡片]", createdAt: "now" },
    ]));
    mocks.backend.queryTranslations.mockResolvedValue({ translations: { cached: "已有缓存译文", "auto reply": "自动接待译文" } });
    await mount();
    expect(mocks.backend.queryTranslations).toHaveBeenCalledTimes(1);
    expect(mocks.backend.queryTranslations).toHaveBeenCalledWith({ texts: ["hello", "cached", "seller text", "auto reply"] });
    expect(workbench.activeConversation?.messages.map((item) => item.translatedContent)).toEqual([undefined, "已有缓存译文", undefined, "自动接待译文", undefined]);
    expect(workbench.translationStats).toMatchObject({ total: 4, translated: 2, untranslated: 2, pending: 0 });
  });

  it("does not query when every textual message is already translated", async () => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([
      { id: "one", role: "buyer", content: "hello", createdAt: "now", translatedContent: "你好" },
      { id: "two", role: "seller", content: "hi", createdAt: "now", translatedContent: "你好呀" },
      { id: "three", role: "card", content: "[卡片]", createdAt: "now" },
    ]));
    await mount();
    expect(mocks.backend.queryTranslations).not.toHaveBeenCalled();
  });

  it.each([
    ["missing", undefined, false],
    ["forced", "旧译文", true],
  ] as const)("runs a %s translation end to end: submit, poll, absorb and clear pending state", async (_, translatedContent, force) => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([
      { id: "one", role: "buyer", content: "hello", createdAt: "now", translatedContent },
    ]));
    mocks.backend.queryTranslations.mockResolvedValue({ translations: {} });
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-1", status: "pending", message: "等待执行" });
    mocks.backend.getTranslationJob.mockResolvedValueOnce({ task_id: "job-1", status: "running", message: "正在执行" });
    await act(async () => {
      if (force) await workbench.translateMessages(workbench.activeConversation!.messages, { force });
      else await workbench.translateMissing();
    });
    expect(mocks.backend.requestTranslations).toHaveBeenCalledExactlyOnceWith({ texts: ["hello"], force, conversationId: 42 });
    expect(workbench.translationPendingIds.has("one")).toBe(true);
    expect(workbench.translationStats.pending).toBe(1);
    // First tick still running; second tick observes success and absorbs from the cache.
    mocks.backend.getTranslationJob.mockResolvedValueOnce({ task_id: "job-1", status: "succeeded", message: "已翻译 1 条" });
    mocks.backend.queryTranslations.mockResolvedValueOnce({ translations: { hello: "你好" } });
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(workbench.activeConversation?.messages[0].translatedContent).toBe("你好");
    expect(workbench.translationPendingIds.size).toBe(0);
    expect(workbench.translationStats).toMatchObject({ translated: 1, untranslated: 0, pending: 0 });
    expect(workbench.translationFailedIds.size).toBe(0);
  });

  it.each([
    ["failed", {}, "error", "翻译失败，可点击消息重试"],
    ["succeeded", { hello: null }, "warning", "有 1 条消息未返回译文，可重试"],
  ] as const)("keeps unresolved messages retryable after a %s job", async (status, translations, level, warning) => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([
      { id: "one", role: "buyer", content: "hello", createdAt: "now" },
      { id: "two", role: "card", content: "[卡片]", createdAt: "now" },
    ]));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-fail", status: "pending", message: "等待执行" });
    mocks.backend.getTranslationJob.mockResolvedValueOnce({ task_id: "job-fail", status, message: "job finished" });
    mocks.backend.queryTranslations.mockResolvedValueOnce({ translations });
    await act(async () => { await workbench.translateMissing(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    // Card bubbles carry no text: only the textual message is submitted.
    expect(mocks.backend.requestTranslations).toHaveBeenCalledExactlyOnceWith({ texts: ["hello"], force: false, conversationId: 42 });
    expect(workbench.translationFailedIds.has("one")).toBe(true);
    expect(workbench.translationPendingIds.size).toBe(0);
    expect(mocks.message[level]).toHaveBeenCalledWith(warning);
    // Retrying submits a fresh job and clears the failed marker.
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-2", status: "pending", message: "等待执行" });
    await act(async () => { await workbench.translateMessages(workbench.activeConversation!.messages); });
    expect(mocks.backend.requestTranslations).toHaveBeenLastCalledWith({ texts: ["hello"], force: false, conversationId: 42 });
    expect(workbench.translationFailedIds.size).toBe(0);
    expect(workbench.translationPendingIds.has("one")).toBe(true);
  });

  it("treats empty NO_NEED sentinels as resolved without failure markers or resubmission", async () => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([
      { id: "one", role: "buyer", content: "hello", createdAt: "now" },
      { id: "two", role: "seller", content: "已经是中文", createdAt: "now" },
    ]));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-mixed", status: "pending", message: "等待执行" });
    mocks.backend.getTranslationJob.mockResolvedValueOnce({ task_id: "job-mixed", status: "succeeded", message: "已翻译 2 条" });
    mocks.backend.queryTranslations.mockResolvedValueOnce({ translations: { hello: "你好", "已经是中文": "" } });
    await act(async () => { await workbench.translateMissing(); });
    expect(mocks.backend.requestTranslations).toHaveBeenCalledExactlyOnceWith({ texts: ["hello", "已经是中文"], force: false, conversationId: 42 });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(workbench.activeConversation?.messages[0].translatedContent).toBe("你好");
    expect(workbench.activeConversation?.messages[1].translatedContent).toBeUndefined();
    // 空串哨兵已解决：不标记失败、不计入缺失、不再重复提交。
    expect(workbench.translationFailedIds.size).toBe(0);
    expect(workbench.translationStats).toMatchObject({ total: 2, translated: 1, untranslated: 0, pending: 0 });
    await act(async () => { await workbench.translateMissing(); });
    expect(mocks.message.info).toHaveBeenCalledWith("译文已齐全");
    expect(mocks.backend.requestTranslations).toHaveBeenCalledTimes(1);
  });

  it("skips job tracking for empty submissions and falls back to a cache read", async () => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([
      { id: "one", role: "buyer", content: "hello", createdAt: "now" },
    ]));
    await mount();
    await act(async () => { await workbench.translateMessages(workbench.activeConversation!.messages); });
    expect(mocks.backend.requestTranslations).toHaveBeenCalledTimes(1);
    // task_id "" triggers an immediate cache absorb instead of polling.
    expect(mocks.backend.queryTranslations).toHaveBeenCalledTimes(2);
    expect(workbench.translationPendingIds.size).toBe(0);
  });

  it("pauses polling while the tab is hidden and resumes afterwards", async () => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([
      { id: "one", role: "buyer", content: "hello", createdAt: "now" },
    ]));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-hidden", status: "pending", message: "等待执行" });
    await act(async () => { await workbench.translateMissing(); });
    Object.defineProperty(document, "hidden", { value: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(mocks.backend.getTranslationJob).not.toHaveBeenCalled();
    Object.defineProperty(document, "hidden", { value: false });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(mocks.backend.getTranslationJob).toHaveBeenCalledTimes(1);
  });

  it("clears translation state when switching conversations and stops polling", async () => {
    const detail = detailWith([{ id: "one", role: "buyer", content: "hello", createdAt: "now" }]);
    mocks.backend.getConversation.mockImplementation(async (id: string) => ({ ...detail, id }));
    mocks.backend.listConversations.mockResolvedValue({
      ...pageOf(), items: [adaptConversationSummary(aggregate), { ...adaptConversationSummary({ ...aggregate, sid: 43 }) }], total: 2,
    });
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "job-switch", status: "pending", message: "等待执行" });
    await act(async () => { await workbench.translateMissing(); });
    expect(workbench.translationPendingIds.size).toBe(1);
    await act(async () => { await workbench.selectConversation("43"); });
    expect(workbench.translationPendingIds.size).toBe(0);
    expect(workbench.translationFailedIds.size).toBe(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(mocks.backend.getTranslationJob).not.toHaveBeenCalled();
  });
});
