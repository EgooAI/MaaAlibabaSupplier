// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BatchActionPanel } from "@/features/chat/batch/BatchActionPanel";
import type { Conversation } from "@/types/chatCanonical";
import type { PortraitTag } from "@/types/portrait";

function conversation(id: string, cid: number | null, options: { stage?: Conversation["customer"]["businessStage"]; tags?: { tag_id: string; name: string; archived: boolean }[] } = {}): Conversation {
  return {
    id,
    customer: {
      id: cid === null ? `c-${id}` : String(cid),
      name: `buyer-${id}`,
      company: "Gulf Trade",
      country: "阿联酋",
      email: "",
      phone: "",
      stage: "unknown",
      tags: [],
      cid,
      businessStage: options.stage ?? "unknown",
      businessTags: options.tags ?? [],
      businessVersion: 1,
      lostReason: null,
      lostNote: null,
      lostAt: null,
      portraitEditable: cid !== null,
      availability: "",
      behavior: [],
    },
    latestMessage: "hello",
    updatedAt: "2026-09-17 10:00",
    unreadCount: 0,
    replyState: "none",
    pendingSince: null,
    dueAt: null,
    isOverdue: false,
    historyPending: false,
    uncertain: false,
  };
}

const portraitTags: PortraitTag[] = [
  { tag_id: "t1", name: "长期合作", description: "", archived: false, version: 1, created_at: 0, updated_at: 0, usage_count: 2 },
  { tag_id: "t2", name: "展会名片", description: "", archived: true, version: 1, created_at: 0, updated_at: 0, usage_count: 1 },
];

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

function findButton(text: string): HTMLButtonElement {
  const button = [...container.querySelectorAll("button")].find((node) => node.textContent?.includes(text));
  if (!button) throw new Error(`button not found: ${text}`);
  return button as HTMLButtonElement;
}

describe("batch action panel", () => {
  it("previews a stage change and applies it as one change set", async () => {
    const onApply = vi.fn();
    const targets = [conversation("42", 1001, { stage: "following_up" }), conversation("43", 1001), conversation("45", null)];
    await act(async () => root.render(
      <BatchActionPanel action="stage" targets={targets} portraitTags={portraitTags} busy={false} onActionChange={vi.fn()} onClose={vi.fn()} onApply={onApply} />,
    ));
    const apply = findButton("应用到");
    expect(apply.disabled).toBe(true);
    expect(container.textContent).toContain("请先设置要变更的内容");
    await act(async () => findButton("报价/洽谈").click());
    expect(container.textContent).toContain("阶段：跟进中 → 报价/洽谈");
    expect(apply.disabled).toBe(false);
    expect(apply.textContent).toContain("应用到 1 位客户（2 个会话）");
    expect(container.textContent).toContain("不可维护");
    await act(async () => apply.click());
    expect(onApply).toHaveBeenCalledExactlyOnceWith({ stage: "negotiating" }, "阶段设为「报价/洽谈」");
  });

  it("requires a lost reason and warns before clearing lost info", async () => {
    const onApply = vi.fn();
    const targets = [conversation("42", 1001, { stage: "lost", tags: [{ tag_id: "t1", name: "长期合作", archived: false }] })];
    await act(async () => root.render(
      <BatchActionPanel action="stage" targets={targets} portraitTags={portraitTags} busy={false} onActionChange={vi.fn()} onClose={vi.fn()} onApply={onApply} />,
    ));
    await act(async () => findButton("流失/失败").click());
    expect(container.textContent).toContain("标记流失需选择失败原因");
    await act(async () => findButton("价格").click());
    await act(async () => findButton("应用到").click());
    expect(onApply).toHaveBeenCalledExactlyOnceWith({ stage: "lost", lost_reason: "price" }, "阶段设为「流失/失败」（原因：价格）");
    await act(async () => findButton("已成交").click());
    expect(container.textContent).toContain("1 位客户当前为流失，提交后将清除失败信息");
  });

  it("applies tag additions with archived options disabled", async () => {
    const onApply = vi.fn();
    await act(async () => root.render(
      <BatchActionPanel action="add-tag" targets={[conversation("42", 1001)]} portraitTags={portraitTags} busy={false} onActionChange={vi.fn()} onClose={vi.fn()} onApply={onApply} />,
    ));
    const boxes = [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    expect(boxes[1].disabled).toBe(true);
    expect(container.textContent).toContain("已停用，不可新增");
    await act(async () => boxes[0].click());
    expect(container.textContent).toContain("＋长期合作");
    await act(async () => findButton("应用到").click());
    expect(onApply).toHaveBeenCalledExactlyOnceWith({ add_tag_ids: ["t1"] }, "增加标签「长期合作」");
  });

  it("aggregates held tags for removal and switches actions through the segmented control", async () => {
    const onApply = vi.fn();
    const onActionChange = vi.fn();
    const targets = [conversation("42", 1001, { tags: [{ tag_id: "t1", name: "长期合作", archived: false }] }), conversation("43", 1001, { tags: [{ tag_id: "t1", name: "长期合作", archived: false }] })];
    await act(async () => root.render(
      <BatchActionPanel action="remove-tag" targets={targets} portraitTags={portraitTags} busy={false} onActionChange={onActionChange} onClose={vi.fn()} onApply={onApply} />,
    ));
    expect(container.textContent).toContain("2 位客户持有");
    await act(async () => container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
    expect(container.textContent).toContain("－长期合作");
    await act(async () => findButton("应用到").click());
    expect(onApply).toHaveBeenCalledExactlyOnceWith({ remove_tag_ids: ["t1"] }, "移除标签「长期合作」");
    const addTab = [...container.querySelectorAll<HTMLInputElement>(".ant-segmented-item-input")];
    expect(addTab.length).toBe(3);
    await act(async () => addTab[1].click());
    expect(onActionChange).toHaveBeenCalledExactlyOnceWith("add-tag");
  });
});
