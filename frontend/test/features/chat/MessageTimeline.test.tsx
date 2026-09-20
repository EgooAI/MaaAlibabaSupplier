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
  it("aligns auto-reception (system) replies on the seller side", async () => {
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
    expect(rowOf("auto reception text").className).toContain("justify-end");
    expect(rowOf("unknown origin text").className).toContain("justify-start");
  });

  it.each(["buyer", "seller", "system"] as const)("translates a %s message and skips cards", async (role) => {
    const onTranslate = vi.fn();
    const target = message({ id: role, role, content: `${role} text` });
    await render({
      messages: [target, message({ id: "card", role: "card", content: "[卡片]" })],
      onTranslate,
      onOpenCard: vi.fn(),
    });
    const translateButtons = [...container.querySelectorAll("button")].filter(button => button.textContent === "翻译");
    expect(translateButtons).toHaveLength(1);
    await act(async () => translateButtons[0].click());
    expect(onTranslate).toHaveBeenCalledExactlyOnceWith(target);
  });

  it("invokes force retranslate from the translation block and retry from the failed marker", async () => {
    const onTranslate = vi.fn();
    await render({
      messages: [
        message({ id: "translated", role: "seller", content: "seller text", translatedContent: "卖家消息译文" }),
        message({ id: "failed", content: "broken" }),
      ],
      failedIds: new Set(["failed"]),
      onTranslate,
      onOpenCard: vi.fn(),
    });
    await act(async () => (container.querySelector('button[aria-label="重新翻译本条消息"]') as HTMLButtonElement).click());
    expect(onTranslate).toHaveBeenLastCalledWith(expect.objectContaining({ id: "translated" }), true);
    const retryButton = [...container.querySelectorAll("button")].find(button => button.textContent === "翻译失败，点击重试");
    expect(retryButton).toBeDefined();
    await act(async () => retryButton!.click());
    expect(onTranslate).toHaveBeenLastCalledWith(expect.objectContaining({ id: "failed", content: "broken" }));
    expect(onTranslate).toHaveBeenCalledTimes(2);
  });

  it("disables inline translation actions while a translation job is in flight", async () => {
    const onTranslate = vi.fn();
    await render({
      messages: [
        message({ id: "pending", content: "in flight" }),
        message({ id: "other", content: "other text" }),
        message({ id: "failed", content: "broken" }),
      ],
      pendingIds: new Set(["pending"]),
      failedIds: new Set(["failed"]),
      onTranslate,
      onOpenCard: vi.fn(),
    });
    expect(container.querySelector('[role="status"]')?.textContent).toContain("翻译中");
    for (const label of ["翻译", "翻译失败，点击重试"]) {
      const action = [...container.querySelectorAll("button")].find(button => button.textContent === label)!;
      expect(action.disabled).toBe(true);
      await act(async () => action.click());
    }
    expect(onTranslate).not.toHaveBeenCalled();
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
});
