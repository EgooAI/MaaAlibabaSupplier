// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessageTimeline } from "@/features/chat/conversation/MessageTimeline";
import type { ChatMessage } from "@/types/chatCanonical";

let root: Root;
let container: HTMLDivElement;

const message = (changes: Partial<ChatMessage>): ChatMessage => ({
  id: "m1",
  role: "buyer",
  content: "hello",
  createdAt: "2026-09-18 10:00",
  ...changes,
});

const render = async (props: Parameters<typeof MessageTimeline>[0]) => {
  await act(async () => root.render(<MessageTimeline {...props} />));
};

const rowOf = (text: string) => {
  const bubble = [...container.querySelectorAll("div")].find(node => node.textContent === text);
  expect(bubble, `bubble for ${text}`).toBeDefined();
  return bubble!.closest(".flex.justify-start, .flex.justify-end") as HTMLElement;
};

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

describe("message timeline", () => {
  it("aligns auto-reception (system) replies on the seller side with seller styling kept distinct", async () => {
    await render({
      messages: [
        message({ id: "buyer", role: "buyer", content: "buyer text" }),
        message({ id: "seller", role: "seller", content: "seller text" }),
        message({ id: "auto", role: "system", content: "auto reception text" }),
        message({ id: "unknown", role: "unknown", content: "unknown origin text" }),
      ],
      onOpenCard: vi.fn(),
    });
    expect(rowOf("buyer text").className).toContain("justify-start");
    expect(rowOf("seller text").className).toContain("justify-end");
    // Auto-reception replies are sent by our own account: right side, but the
    // system bubble style stays grey to distinguish them from real seller text.
    expect(rowOf("auto reception text").className).toContain("justify-end");
    expect(rowOf("auto reception text").querySelector(".bg-slate-100")).not.toBeNull();
    expect(rowOf("seller text").querySelector(".bg-blue-50")).not.toBeNull();
    expect(rowOf("unknown origin text").className).toContain("justify-start");
  });

  it("offers translation affordances for every textual message and skips cards", async () => {
    await render({
      messages: [
        message({ id: "buyer", role: "buyer", content: "buyer text" }),
        message({ id: "seller", role: "seller", content: "seller text" }),
        message({ id: "auto", role: "system", content: "auto text" }),
        message({ id: "card", role: "card", content: "[卡片]" }),
      ],
      onTranslate: vi.fn(),
      onOpenCard: vi.fn(),
    });
    const translateButtons = [...container.querySelectorAll("button")].filter(button => button.textContent === "翻译");
    expect(translateButtons).toHaveLength(3);
    expect([...container.querySelectorAll("button")].some(button => button.textContent?.includes("卡片"))).toBe(false);
  });

  it("renders the translation block for any party and hides it via showTranslations", async () => {
    const messages = [
      message({ id: "seller", role: "seller", content: "seller text", translatedContent: "卖家消息译文" }),
      message({ id: "auto", role: "system", content: "auto text", translatedContent: "自动接待译文" }),
    ];
    await render({ messages, showTranslations: true, onOpenCard: vi.fn() });
    expect(container.textContent).toContain("卖家消息译文");
    expect(container.textContent).toContain("自动接待译文");
    expect(container.querySelectorAll('button[aria-label="重新翻译本条消息"]')).toHaveLength(2);
    await render({ messages, showTranslations: false, onOpenCard: vi.fn() });
    expect(container.textContent).not.toContain("卖家消息译文");
  });

  it("shows inline pending and failed translation states", async () => {
    await render({
      messages: [
        message({ id: "pending", content: "in flight" }),
        message({ id: "failed", content: "broken" }),
      ],
      pendingIds: new Set(["pending"]),
      failedIds: new Set(["failed"]),
      onTranslate: vi.fn(),
      onOpenCard: vi.fn(),
    });
    expect(container.textContent).toContain("翻译中…");
    expect(container.textContent).toContain("翻译失败，点击重试");
  });
});
