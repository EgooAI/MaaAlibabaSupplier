// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BatchCustomerDrawer } from "@/features/chat/batch/BatchCustomerDrawer";
import { BatchCustomerGrid } from "@/features/chat/batch/BatchCustomerGrid";
import { groupCustomers } from "@/domain/portrait/customerGroups";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import { authenticatedSession } from "@/test/support/authFixture";
import type { Conversation } from "@/types/chatCanonical";
import type { PortraitStage, PortraitTag } from "@/types/portrait";

const mocks = vi.hoisted(() => ({
  backend: { listPortraitOperations: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));

if (!globalThis.ResizeObserver) {
  Object.assign(globalThis, {
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}

function conversation(id: string, cid: number | null, options: { unread?: number; reply?: "needs_reply" | "none"; name?: string; stage?: PortraitStage; tags?: { tag_id: string; name: string; archived: boolean }[]; version?: number } = {}): Conversation {
  return {
    id,
    customer: {
      id: cid === null ? `c-${id}` : String(cid),
      name: options.name ?? `buyer-${id}`,
      company: "Gulf Trade",
      country: "阿联酋",
      email: "buyer@example.com",
      phone: "+971 50 000 0000",
      stage: "unknown",
      tags: ["金牌采购商"],
      cid,
      businessStage: options.stage ?? "unknown",
      businessTags: options.tags ?? [],
      businessVersion: options.version ?? 0,
      lostReason: null,
      lostNote: null,
      lostAt: null,
      portraitEditable: cid !== null,
      availability: "",
      behavior: [],
    },
    latestMessage: `message-${id}`,
    updatedAt: "2026-09-17 10:00",
    unreadCount: options.unread ?? 0,
    replyState: options.reply ?? "none",
    pendingSince: null,
    dueAt: null,
    isOverdue: false,
    historyPending: false,
    uncertain: false,
  };
}

const tag = (tag_id: string, name: string, archived = false): { tag_id: string; name: string; archived: boolean } => ({ tag_id, name, archived });

const fixture: Conversation[] = [
  conversation("42", 1001, { name: "Ahmed", unread: 2, reply: "needs_reply", stage: "negotiating", tags: [tag("t1", "长期合作")], version: 3 }),
  conversation("43", 1001, { name: "Ahmed", unread: 1 }),
  conversation("44", 1002, { name: "Maria", stage: "following_up" }),
  conversation("45", null),
];

let root: Root;
let container: HTMLDivElement;

beforeEach(async () => {
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  accountSession.invalidate();
  localStorage.clear();
  await authenticatedSession();
  accountSession.accept(structuredClone(connectionSnapshot));
  mocks.backend.listPortraitOperations.mockResolvedValue({ operations: [] });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("batch customer grid", () => {
  it("renders one row per customer, merges unresolved sessions and shows portrait state", async () => {
    await act(async () => root.render(
      <BatchCustomerGrid conversations={fixture} selectedIds={[]} onToggleSessions={vi.fn()} onToggleAll={vi.fn()} onOpenCustomer={vi.fn()} />,
    ));
    const rows = [...container.querySelectorAll<HTMLTableRowElement>("tr[data-row-key]")];
    expect(rows.map((row) => row.getAttribute("data-row-key"))).toEqual(["c:1001", "c:1002", "na"]);
    expect(rows[0].textContent).toContain("Ahmed");
    expect(rows[0].textContent).toContain("报价/洽谈");
    expect(rows[0].textContent).toContain("长期合作");
    expect(rows[0].textContent).toContain("v3");
    expect(rows[0].textContent).toContain("未读 3");
    expect(rows[0].textContent).toContain("待回复 1");
    expect(rows[1].textContent).toContain("Maria");
    expect(rows[1].textContent).toContain("未维护");
    expect(rows[2].textContent).toContain("未识别客户");
    expect(rows[2].textContent).toContain("不可维护");
    expect(rows[2].textContent).toContain("cid 缺失");
  });

  it("toggles whole customers, header select-all and expands session sub-rows", async () => {
    const onToggleSessions = vi.fn();
    const onToggleAll = vi.fn();
    await act(async () => root.render(
      <BatchCustomerGrid conversations={fixture} selectedIds={["42"]} onToggleSessions={onToggleSessions} onToggleAll={onToggleAll} onOpenCustomer={vi.fn()} />,
    ));
    const rowCheckbox = container.querySelector<HTMLInputElement>('input[aria-label="选择 Ahmed"]')!;
    expect(rowCheckbox.checked).toBe(false);
    await act(async () => rowCheckbox.click());
    expect(onToggleSessions).toHaveBeenCalledExactlyOnceWith(["42", "43"], true);
    const header = container.querySelector<HTMLInputElement>('input[aria-label="全选当前页"]')!;
    expect(header.checked).toBe(false);
    await act(async () => header.click());
    expect(onToggleAll).toHaveBeenCalledExactlyOnceWith(true);
    await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="展开会话"]')!.click());
    expect(container.textContent).toContain("会话 #42");
    expect(container.textContent).toContain("会话 #43");
    // 收起后展开态回到按钮标签（行内容由 antd 动画负责卸载）。
    await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label="收起会话"]')!.click());
    expect(container.querySelectorAll('button[aria-label="展开会话"]').length).toBe(3);
  });

  it("opens the customer drawer from a row or the history button, but not from controls", async () => {
    const onOpenCustomer = vi.fn();
    await act(async () => root.render(
      <BatchCustomerGrid conversations={fixture} selectedIds={[]} onToggleSessions={vi.fn()} onToggleAll={vi.fn()} onOpenCustomer={onOpenCustomer} />,
    ));
    const row = container.querySelector<HTMLTableRowElement>('tr[data-row-key="c:1001"]')!;
    await act(async () => row.querySelector<HTMLInputElement>('input[aria-label="选择 Ahmed"]')!.click());
    expect(onOpenCustomer).not.toHaveBeenCalled();
    await act(async () => row.querySelector<HTMLButtonElement>('button[aria-label="查看操作历史"]')!.click());
    expect(onOpenCustomer).toHaveBeenCalledExactlyOnceWith("c:1001");
    await act(async () => [...row.querySelectorAll("span")].find((node) => node.textContent === "Ahmed")!.click());
    expect(onOpenCustomer).toHaveBeenCalledTimes(2);
    expect(onOpenCustomer).toHaveBeenLastCalledWith("c:1001");
  });
});

describe("batch customer drawer", () => {
  const portraitTags: PortraitTag[] = [{ tag_id: "t1", name: "长期合作", description: "", archived: false, version: 1, created_at: 0, updated_at: 0, usage_count: 1 }];

  it("shows portrait details, page sessions and the operation history", async () => {
    mocks.backend.listPortraitOperations.mockResolvedValue({
      operations: [{ request_id: "req-1", kind: "batch", created_at: 1756720000, changes: { stage: "negotiating" }, targets: [{ cid: 1001, sids: [42], version: 4 }] }],
    });
    const group = groupCustomers([fixture[0]])[0];
    await act(async () => root.render(<BatchCustomerDrawer group={group} open onClose={vi.fn()} portraitTags={portraitTags} />));
    await act(async () => {});
    expect(mocks.backend.listPortraitOperations).toHaveBeenCalledExactlyOnceWith(1001);
    expect(document.body.textContent).toContain("客户详情");
    expect(document.body.textContent).toContain("报价/洽谈");
    expect(document.body.textContent).toContain("长期合作");
    expect(document.body.textContent).toContain("会话 #42");
    expect(document.body.textContent).toContain("阶段→报价/洽谈");
  });

  it("explains unresolved customers without fetching a history", async () => {
    const group = groupCustomers([fixture[3]])[0];
    await act(async () => root.render(<BatchCustomerDrawer group={group} open onClose={vi.fn()} portraitTags={portraitTags} />));
    await act(async () => {});
    expect(document.body.textContent).toContain("cid 缺失");
    expect(mocks.backend.listPortraitOperations).not.toHaveBeenCalled();
  });
});
