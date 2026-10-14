// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { useBatchManagement } from "@/features/chat/hooks/useBatchManagement";
import { accountSession } from "@/services/accountSession";
import { ApiError } from "@/services/httpAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import { adaptConversationDetail } from "@/services/chatAdapter";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { ConversationPage } from "@/types/inbox";
import { authenticatedSession } from "@/test/support/authFixture";

const mocks = vi.hoisted(() => ({
  message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
  backend: {
    getConnection: vi.fn(),
    getConversationRevision: vi.fn(),
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    listPortraitTags: vi.fn(),
    savePortraitBatch: vi.fn(),
    exportPortraits: vi.fn(),
  },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => ({
  App: {
    useApp: () => ({ message: mocks.message }),
  },
}));

const portraitDetail = (id: string, cid: number | null, version = 0): ConversationDetail => {
  const base = adaptConversationDetail({
    sid: Number(id), name: `buyer-${id}`, participants: [], messages: [], latest: { content: "hello", updated_at: "2026-09-17 10:00" },
    unread_count: 0, reply_state: "none", pending_since: null, due_at: null, is_overdue: false, history_pending: false, uncertain: false,
  });
  return {
    ...base,
    customer: {
      ...base.customer,
      cid,
      businessStage: "unknown",
      businessTags: [],
      businessVersion: version,
      lostReason: null,
      lostNote: null,
      lostAt: null,
      portraitEditable: cid !== null,
    },
  };
};

const page = (items: ConversationDetail[], offset = 0, total = items.length, token = "page-1"): ConversationPage => ({
  items, total, offset, limit: 50, inbox_revision: 1, portrait_revision: 0, tag_revision: 0, pagination_revision: token,
});
const revision = { ...connectionSnapshot.source, ready: true, inbox_revision: 1, portrait_revision: 0, tag_revision: 0, next_due_at: null };

let root: Root;
let container: HTMLDivElement;
let batch: ReturnType<typeof useBatchManagement>;

function Batch() {
  const current = useBatchManagement();
  useEffect(() => { batch = current; }, [current]);
  return <output>{current.selectedIds.join(",")}</output>;
}
function Workspace() {
  const current = useAccount();
  if ((current.blocked && !current.suspended) || !current.snapshot?.capabilities.read_chat) return null;
  return <Batch key={`${current.snapshot.account.epoch}:${current.generation}`} />;
}
async function mount() {
  await act(async () => root.render(<AccountProvider><Workspace /></AccountProvider>));
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  mocks.backend.getConnection.mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.getConversationRevision.mockResolvedValue(revision);
  mocks.backend.listPortraitTags.mockResolvedValue({ tags: [] });
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

describe("portrait batch workflow", () => {
  it("submits selected sessions with their resolved customer versions and refreshes after success", async () => {
    mocks.backend.listConversations.mockResolvedValue(page([portraitDetail("42", 1001, 3), portraitDetail("43", 1001, 3)]));
    mocks.backend.savePortraitBatch.mockResolvedValue({
      request_id: "req", portrait_revision: 1, tag_revision: 0,
      changed: [{ cid: 1001, sids: [42, 43], version: 4 }], unchanged: [],
    });
    await mount();
    await act(async () => batch.toggleSelected("42"));
    await act(async () => batch.toggleSelected("43"));
    expect(batch.selectedCount).toBe(2);
    expect(batch.selectedCustomerCount).toBe(1);
    await act(async () => batch.openPanel("stage"));
    expect(batch.panel).toEqual({ action: "stage", sessionIds: ["42", "43"] });
    const calls = mocks.backend.listConversations.mock.calls.length;
    await act(async () => batch.applyPortraitChanges({ stage: "won" }, "阶段设为「已成交」"));
    expect(mocks.backend.savePortraitBatch).toHaveBeenCalledTimes(1);
    expect(batch.panel).toBeNull();
    const payload = mocks.backend.savePortraitBatch.mock.calls[0][0];
    expect(payload.changes).toEqual({ stage: "won" });
    expect(payload.targets).toEqual([
      { sid: 42, expected_cid: 1001, expected_version: 3 },
      { sid: 43, expected_cid: 1001, expected_version: 3 },
    ]);
    expect(payload.request_id).toBeTruthy();
    expect(mocks.message.success).toHaveBeenCalled();
    expect(batch.selectedIds).toEqual([]);
    expect(mocks.backend.listConversations.mock.calls.length).toBeGreaterThan(calls);
    // Each action generates a fresh request id instead of reusing one receipt.
    await act(async () => batch.selectAll());
    await act(async () => batch.applyPortraitChanges({ stage: "negotiating" }, "阶段设为「报价/洽谈」"));
    expect(mocks.backend.savePortraitBatch.mock.calls[1][0].request_id).not.toBe(payload.request_id);
  });

  it("opens the portrait panel for the current selection or a single customer row", async () => {
    mocks.backend.listConversations.mockResolvedValue(page([portraitDetail("42", 1001, 3), portraitDetail("43", 1001, 3)]));
    await mount();
    await act(async () => batch.openPanel("stage"));
    expect(batch.panel).toBeNull();
    expect(mocks.message.warning).toHaveBeenCalled();
    await act(async () => batch.toggleSelected("42"));
    await act(async () => batch.openPanel("stage"));
    expect(batch.panel).toEqual({ action: "stage", sessionIds: ["42"] });
    await act(async () => batch.openPanel("add-tag", ["42", "43"]));
    expect(batch.panel).toEqual({ action: "add-tag", sessionIds: ["42", "43"] });
    await act(async () => batch.changePanelAction("remove-tag"));
    expect(batch.panel).toEqual({ action: "remove-tag", sessionIds: ["42", "43"] });
    await act(async () => batch.closePanel());
    expect(batch.panel).toBeNull();
  });

  it("skips sessions without a resolved customer instead of sending guessed targets", async () => {
    mocks.backend.listConversations.mockResolvedValue(page([portraitDetail("42", null)]));
    await mount();
    await act(async () => batch.selectAll());
    await act(async () => batch.applyPortraitChanges({ stage: "won" }, "阶段设为「已成交」"));
    expect(mocks.backend.savePortraitBatch).not.toHaveBeenCalled();
    expect(mocks.message.warning).toHaveBeenCalled();
  });

  it("reports version conflicts and re-reads the workspace before retrying", async () => {
    mocks.backend.listConversations
      .mockResolvedValueOnce(page([portraitDetail("42", 1001, 1)]))
      .mockResolvedValue(page([portraitDetail("42", 1001, 2)]));
    mocks.backend.savePortraitBatch.mockRejectedValue(new ApiError("画像目标已变化，请刷新后重试", "/api/portraits/batch", { status: 409 }));
    await mount();
    await act(async () => batch.selectAll());
    const calls = mocks.backend.listConversations.mock.calls.length;
    await act(async () => batch.applyPortraitChanges({ add_tag_ids: ["tag-1"] }, "增加标签「价格敏感」"));
    expect(mocks.message.error).toHaveBeenCalled();
    expect(mocks.backend.listConversations.mock.calls.length).toBeGreaterThan(calls);
    expect(batch.conversations[0].customer.businessVersion).toBe(2);
  });

  it("exports deduplicated customer portraits for the current page selection", async () => {
    mocks.backend.listConversations.mockResolvedValue(page([portraitDetail("42", 1001), portraitDetail("43", 1001), portraitDetail("44", null)]));
    mocks.backend.exportPortraits.mockResolvedValue({ filename: "customer-portraits.csv", content: "\ufeffcid\n", exported: 1, missing: [44] });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    await mount();
    await act(async () => batch.toggleSelected("42"));
    await act(async () => batch.toggleSelected("44"));
    await act(async () => batch.exportPortraits());
    expect(mocks.backend.exportPortraits).toHaveBeenCalledExactlyOnceWith({ conversationIds: [42] });
    expect(mocks.message.success).toHaveBeenCalled();
  });

  it("applies and exports the panel's own sessions when opened from a single customer row", async () => {
    mocks.backend.listConversations.mockResolvedValue(page([portraitDetail("42", 1001, 3), portraitDetail("43", 1002, 0)]));
    mocks.backend.savePortraitBatch.mockResolvedValue({
      request_id: "req", portrait_revision: 1, tag_revision: 0,
      changed: [{ cid: 1001, sids: [42], version: 4 }], unchanged: [],
    });
    mocks.backend.exportPortraits.mockResolvedValue({ filename: "customer-portraits.csv", content: "\ufeffcid\n", exported: 1, missing: [] });
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:test");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    await mount();
    await act(async () => batch.openPanel("stage", ["42"]));
    await act(async () => batch.applyPortraitChanges({ stage: "won" }, "阶段设为「已成交」"));
    expect(mocks.backend.savePortraitBatch.mock.calls[0][0].targets).toEqual([{ sid: 42, expected_cid: 1001, expected_version: 3 }]);
    await act(async () => batch.openPanel("export-portrait", ["42"]));
    await act(async () => batch.exportPortraits());
    expect(mocks.backend.exportPortraits).toHaveBeenCalledExactlyOnceWith({ conversationIds: [42] });
  });
});
