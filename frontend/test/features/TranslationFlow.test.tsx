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
import { ApiError } from "@/services/httpAdapter";

const mocks = vi.hoisted(() => ({
  message: { warning: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn() },
  backend: { getConnection: vi.fn(), getConversationRevision: vi.fn(), listConversations: vi.fn(), getConversation: vi.fn(), markConversationRead: vi.fn(), requestTranslations: vi.fn(), queryTranslations: vi.fn(), getTranslationJob: vi.fn(), analyzeConversation: vi.fn(), getAssistantSuggestions: vi.fn() },
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
  it.each(["analysis", "suggestions"])("retains uncertain %s results without claiming a recoverable history", async (operation) => {
    await mount();
    const request = operation === "analysis" ? mocks.backend.analyzeConversation : mocks.backend.getAssistantSuggestions;
    request.mockRejectedValue(new ApiError("timeout", "/api/conversations/42/ai", { kind: "timeout", requestId: "ai-lost" }));
    await act(async () => { if (operation === "analysis") await workbench.analyzeConversation(); else await workbench.openSuggestions(); });
    const text = operation === "analysis" ? workbench.analysisError : workbench.suggestionError;
    expect(text).toContain("结果未知");
    expect(text).toContain("无法重新读取");
    expect(text).toContain("ai-lost");
    expect(request).toHaveBeenCalledTimes(1);
  });

  it.each(["missing", "deadline", "cache failure"])("reports %s as unobserved, without marking failure or resubmitting", async (reason) => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([{ id: "one", role: "buyer", content: "hello", createdAt: "now" }]));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "unknown-job", status: "pending" });
    mocks.backend.getTranslationJob.mockResolvedValue(reason === "missing" ? null : { task_id: "unknown-job", status: reason === "deadline" ? "running" : "succeeded" });
    if (reason === "cache failure") mocks.backend.queryTranslations.mockRejectedValue(new Error("cache unavailable"));
    await act(async () => workbench.translateMissing());
    if (reason === "deadline") vi.setSystemTime(Date.now() + 300001);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(workbench.translationNotice).toContain(reason === "cache failure" ? "缓存查询失败" : "翻译结果未知");
    expect(workbench.translationPendingIds.size).toBe(0);
    expect(workbench.translationFailedIds.size).toBe(0);
    expect(mocks.backend.requestTranslations).toHaveBeenCalledTimes(1);
    expect(mocks.message.info).not.toHaveBeenCalledWith("翻译仍在后台进行，稍后刷新会自动显示");
  });

  it("waits for all accepted batches, including when the last batch completes first", async () => {
    const messages = Array.from({ length: 501 }, (_, index) => ({ id: String(index), role: "buyer" as const, content: `text-${index}`, createdAt: "now" }));
    mocks.backend.getConversation.mockResolvedValue(detailWith(messages));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "batch-1" }).mockResolvedValueOnce({ task_id: "batch-2" });
    mocks.backend.getTranslationJob.mockImplementation(async (id) => ({ task_id: id, status: id === "batch-1" ? "running" : "succeeded" }));
    await act(async () => workbench.translateMissing());
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(workbench.translationStats.pending).toBe(501);
    expect(mocks.backend.getTranslationJob.mock.calls.map(([id]) => id)).toEqual(["batch-1", "batch-2"]);
    mocks.backend.getTranslationJob.mockResolvedValue({ task_id: "batch-1", status: "succeeded" });
    mocks.backend.queryTranslations.mockImplementation(async ({ texts }: { texts: string[] }) => ({ translations: Object.fromEntries(texts.map((text) => [text, ""])) }));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(workbench.translationStats).toMatchObject({ pending: 0, untranslated: 0 });
    expect(mocks.backend.requestTranslations).toHaveBeenCalledTimes(2);
  });

  it("does not apply an old completion after reselecting the same conversation", async () => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([{ id: "one", role: "buyer", content: "hello", createdAt: "now" }]));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "old-job" });
    mocks.backend.getTranslationJob.mockResolvedValueOnce({ task_id: "old-job", status: "succeeded" });
    let resolve!: (value: { translations: Record<string, string> }) => void;
    mocks.backend.queryTranslations.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    await act(async () => workbench.translateMissing());
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await act(async () => workbench.selectConversation("42"));
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "new-job" });
    await act(async () => workbench.translateMissing());
    await act(async () => resolve({ translations: { hello: "old result" } }));
    expect(workbench.activeConversation?.messages[0].translatedContent).toBeUndefined();
    expect(workbench.translationPendingIds.has("one")).toBe(true);
  });

  it("continues observing accepted work after a later batch loses its receipt", async () => {
    const messages = Array.from({ length: 501 }, (_, index) => ({ id: String(index), role: "buyer" as const, content: `text-${index}`, createdAt: "now" }));
    mocks.backend.getConversation.mockResolvedValue(detailWith(messages));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "accepted" }).mockRejectedValueOnce(new ApiError("connection lost", "/api/messages/translations", { kind: "transport", requestId: "batch-2" }));
    await act(async () => workbench.translateMissing());
    expect(workbench.translationStats.pending).toBe(500);
    expect(workbench.translationNotice).toContain("结果未知");
    expect(workbench.translationNotice).toContain("batch-2");
    mocks.backend.getTranslationJob.mockResolvedValue({ task_id: "accepted", status: "succeeded" });
    mocks.backend.queryTranslations.mockImplementation(async ({ texts }: { texts: string[] }) => ({ translations: Object.fromEntries(texts.map((text) => [text, ""])) }));
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(workbench.translationStats).toMatchObject({ pending: 0, untranslated: 1 });
    expect(workbench.translationNotice).toContain("结果未知");
    expect(mocks.backend.requestTranslations).toHaveBeenCalledTimes(2);
  });

  it("never overlaps slow translation observations", async () => {
    mocks.backend.getConversation.mockResolvedValue(detailWith([{ id: "one", role: "buyer", content: "hello", createdAt: "now" }]));
    await mount();
    mocks.backend.requestTranslations.mockResolvedValueOnce({ task_id: "slow" });
    let resolve!: (value: null) => void;
    mocks.backend.getTranslationJob.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    await act(async () => workbench.translateMissing());
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(mocks.backend.getTranslationJob).toHaveBeenCalledTimes(1);
    await act(async () => resolve(null));
    expect(workbench.translationNotice).toContain("结果未知");
  });

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
