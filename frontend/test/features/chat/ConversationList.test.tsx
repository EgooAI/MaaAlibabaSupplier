// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ConversationList } from "@/features/chat/conversation/ConversationList";
import type { Conversation } from "@/types/chatCanonical";

if (!globalThis.ResizeObserver) {
  Object.assign(globalThis, {
    ResizeObserver: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}
if (!window.matchMedia) {
  Object.assign(window, {
    matchMedia: () => ({
      matches: false, media: "", onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  });
}

const conversation = (overrides: Partial<Conversation> = {}): Conversation => ({
  id: "42",
  customer: {
    id: "1001",
    name: "Tiran Ghevondyan",
    company: "",
    country: "ID",
    email: "",
    phone: "",
    stage: "unknown",
    tags: [],
    cid: null,
    businessStage: "unknown",
    businessTags: [],
    businessVersion: 0,
    lostReason: null,
    lostNote: null,
    lostAt: null,
    portraitEditable: false,
    availability: "",
    behavior: [],
  },
  latestMessage: "[图片]",
  updatedAt: new Date().toISOString(),
  unreadCount: 0,
  replyState: "waiting_customer",
  pendingSince: null,
  historyPending: false,
  uncertain: false,
  ...overrides,
});

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
});

const render = async (item: Conversation) => {
  await act(async () => root.render(<ConversationList conversations={[item]} />));
};

describe("ConversationList country marker", () => {
  it("shows only the flag before the latest message, without the ISO code", async () => {
    await render(conversation());

    const preview = container.querySelector<HTMLElement>('[data-testid="conversation-preview"]');
    expect(preview).not.toBeNull();

    const marker = preview!.querySelector<HTMLElement>('[data-country-code="ID"]');
    expect(marker).not.toBeNull();
    expect(marker!.querySelector(".fi-id")).not.toBeNull();
    expect(marker!.getAttribute("title")).toBe("印度尼西亚");
    expect(preview!.textContent).toBe("[图片]");

    const badges = container.querySelector<HTMLElement>('[data-testid="conversation-badges"]');
    expect(badges).not.toBeNull();
    expect(badges!.textContent).not.toContain("ID");
  });

  it("resolves a full country name to its flag", async () => {
    await render(conversation({ customer: { ...conversation().customer, country: "Spain" } }));

    const marker = container.querySelector<HTMLElement>('[data-country-code="ES"]');
    expect(marker).not.toBeNull();
    expect(marker!.querySelector(".fi-es")).not.toBeNull();
    expect(marker!.textContent).toBe("");
  });

  it("renders no marker for an unknown country value", async () => {
    await render(conversation({ customer: { ...conversation().customer, country: "Straße" } }));

    expect(container.querySelector("[data-country-code]")).toBeNull();
    expect(container.querySelector('[data-testid="conversation-preview"]')!.textContent).toBe("[图片]");
  });
});

describe("ConversationList routine status badges", () => {
  it.each([
    ["waiting_customer", "等待客户"],
    ["none", "无需回复"],
  ] as const)("hides the %s status tag", async (replyState, label) => {
    await render(conversation({ replyState }));

    const badges = container.querySelector<HTMLElement>('[data-testid="conversation-badges"]')!;
    expect(badges.textContent).not.toContain(label);
  });

  it.each([
    ["needs_reply", "待回复"],
    ["history_pending", "历史待确认"],
    ["unknown", "回复状态未知"],
  ] as const)("keeps the %s status tag", async (replyState, label) => {
    await render(conversation({ replyState }));

    const badges = container.querySelector<HTMLElement>('[data-testid="conversation-badges"]')!;
    expect(badges.textContent).toContain(label);
  });
});
