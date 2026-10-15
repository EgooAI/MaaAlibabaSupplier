// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider } from "@/features/account/AccountProvider";
import { TagManagerModal } from "@/features/chat/portrait/TagManagerModal";
import { useBatchManagement } from "@/features/chat/hooks/useBatchManagement";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import { adaptConversationDetail } from "@/services/chatAdapter";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { ConversationPage } from "@/types/inbox";
import type { PortraitTag } from "@/types/portrait";
import { authenticatedSession } from "@/test/support/authFixture";

const mocks = vi.hoisted(() => ({
  message: { error: vi.fn(), warning: vi.fn(), success: vi.fn(), info: vi.fn() },
  backend: {
    getConnection: vi.fn(),
    getConversationRevision: vi.fn(),
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    listPortraitTags: vi.fn(),
    updatePortraitTag: vi.fn(),
  },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  // Keep real components (modal, inputs) but route notices into assertions.
  return { ...actual, App: { useApp: () => ({ message: mocks.message }) } };
});

if (!globalThis.ResizeObserver) {
  Object.assign(globalThis, {
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}

const tag = (tagId: string, name: string, overrides: Partial<PortraitTag> = {}): PortraitTag => ({
  tag_id: tagId, name, description: "", archived: false, version: 1,
  created_at: 1756720000, updated_at: 1756720000, usage_count: 2, ...overrides,
});

const conversation = (id: string, cid: number | null, version = 0): ConversationDetail => {
  const base = adaptConversationDetail({
    sid: Number(id), name: `buyer-${id}`, participants: [], messages: [], latest: { content: "hello", updated_at: "2026-09-17 10:00" },
    unread_count: 0, reply_state: "none", pending_since: null, due_at: null, is_overdue: false, history_pending: false, uncertain: false,
  });
  return { ...base, customer: { ...base.customer, cid, businessVersion: version, portraitEditable: cid !== null } };
};

const page = (items: ConversationDetail[]): ConversationPage => ({
  items, total: items.length, offset: 0, limit: 50, inbox_revision: 1,
  portrait_revision: 0, tag_revision: 0, pagination_revision: "page-1",
});
const revision = { ...connectionSnapshot.source, ready: true, inbox_revision: 1, portrait_revision: 0, tag_revision: 0, next_due_at: null };

let root: Root;
let container: HTMLDivElement;

function Batch() {
  useBatchManagement();
  return <output />;
}

function Mount({ children }: { children?: ReactNode }) {
  return <AccountProvider>{children ?? <Batch />}</AccountProvider>;
}

function inputValue(selector: string, value: string) {
  const input = document.querySelector<HTMLInputElement>(selector);
  if (!input) throw new Error(`missing input ${selector}`);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function buttonByText(text: string): HTMLButtonElement {
  // antd inserts a space between the two CJK chars of short buttons ("编 辑").
  const button = [...document.querySelectorAll("button")]
    .find((candidate) => candidate.textContent?.replace(/\s/g, "").includes(text));
  if (!button) throw new Error(`missing button ${text}`);
  return button;
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  accountSession.accept(structuredClone(connectionSnapshot));
  mocks.backend.getConnection.mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.getConversationRevision.mockResolvedValue({ ...revision });
  mocks.backend.listConversations.mockResolvedValue(page([conversation("42", 1001)]));
  mocks.backend.listPortraitTags.mockResolvedValue({ tags: [] });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("shared tag catalog revision", () => {
  it("reloads the catalog when another workspace advances the shared tag revision", async () => {
    await act(async () => root.render(<Mount />));
    await act(async () => { });
    expect(mocks.backend.listPortraitTags).toHaveBeenCalledOnce();
    // A poll that only re-observes the same revision is a no-op baseline.
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listPortraitTags).toHaveBeenCalledOnce();
    mocks.backend.getConversationRevision.mockReset().mockResolvedValue({ ...revision, tag_revision: 3 });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.backend.listPortraitTags).toHaveBeenCalledTimes(2);
  });
});

describe("tag manager modal", () => {
  function renderManager(open: boolean, onChanged = vi.fn()) {
    return act(async () => root.render(
      <Mount>
        <TagManagerModal
          open={open}
          onClose={vi.fn()}
          tags={[tag("t1", "高潜", { description: "复购意向强", usage_count: 3 })]}
          onChanged={onChanged}
        />
      </Mount>,
    ));
  }

  it("renames a tag and refreshes the shared catalog afterwards", async () => {
    const onChanged = vi.fn();
    mocks.backend.updatePortraitTag.mockResolvedValue({ tag: tag("t1", "高潜客户") });
    await renderManager(true, onChanged);
    await act(async () => buttonByText("编辑").click());
    await act(async () => {
      inputValue("input[aria-label='标签名称：高潜']", "高潜客户");
      inputValue("input[aria-label='标签说明：高潜']", "复购意向强且客单高");
    });
    await act(async () => buttonByText("保存").click());
    expect(mocks.backend.updatePortraitTag).toHaveBeenCalledWith("t1", { name: "高潜客户", description: "复购意向强且客单高" });
    expect(mocks.message.success).toHaveBeenCalledWith("标签已更新");
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("reports duplicate names from the shared dictionary without changing the tag", async () => {
    mocks.backend.updatePortraitTag.mockRejectedValue(new Error("同名标签已存在"));
    await renderManager(true);
    await act(async () => buttonByText("编辑").click());
    await act(async () => { inputValue("input[aria-label='标签名称：高潜']", "已占用"); });
    await act(async () => buttonByText("保存").click());
    expect(mocks.message.error).toHaveBeenCalledWith("同名标签已存在");
    // The modal keeps showing the untouched shared name after the failed save.
    expect(document.body.textContent).toContain("高潜");
  });

  it("archives without touching the name", async () => {
    const onChanged = vi.fn();
    mocks.backend.updatePortraitTag.mockResolvedValue({ tag: tag("t1", "高潜", { archived: true }) });
    await renderManager(true, onChanged);
    await act(async () => buttonByText("停用").click());
    expect(mocks.backend.updatePortraitTag).toHaveBeenCalledWith("t1", { archived: true });
    expect(mocks.message.success).toHaveBeenCalledWith("标签已停用，历史关联保留");
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("treats an unchanged editor save as a no-op instead of an empty patch", async () => {
    await renderManager(true);
    await act(async () => buttonByText("编辑").click());
    await act(async () => buttonByText("保存").click());
    expect(mocks.backend.updatePortraitTag).not.toHaveBeenCalled();
    expect(mocks.message.info).toHaveBeenCalledWith("没有需要保存的修改");
  });
});
