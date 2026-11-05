// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessageTimeline } from "@/features/chat/conversation/MessageTimeline";
import { ApiError } from "@/services/httpAdapter";
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
  vi.unstubAllGlobals();
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
    const translateButtons = container.querySelectorAll<HTMLButtonElement>('button[aria-label="翻译本条消息"]');
    expect(translateButtons).toHaveLength(1);
    await act(async () => translateButtons[0].click());
    expect(onTranslate).toHaveBeenCalledExactlyOnceWith(target);
  });

  it("loads image messages through the account backend, skips translation, and reports expired files", async () => {
    const original = { create: URL.createObjectURL, revoke: URL.revokeObjectURL };
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:chat-image"), revokeObjectURL: vi.fn() });
    vi.stubGlobal("IntersectionObserver", class {
      constructor(private readonly callback: (entries: { isIntersecting: boolean }[]) => void) {}
      observe() { this.callback([{ isIntersecting: true }]); }
      disconnect() {}
    });
    const loadImage = vi.fn(async (_conversationId: string, externalMid: string) => {
      if (externalMid === "gone") throw new ApiError("图片已失效或已被平台删除", "/image", { status: 404 });
      return new Blob(["x"], { type: "image/png" });
    });
    try {
      await render({
        messages: [
          message({ id: "pic", type: "image", content: "[图片]", image: { width: 400, height: 300 } }),
          message({ id: "gone", type: "image", content: "[图片]", image: { width: 400, height: 300 } }),
        ],
        conversationId: "42",
        loadImage,
        onTranslate: vi.fn(),
        onOpenCard: vi.fn(),
      });
      expect(loadImage.mock.calls).toEqual([["42", "pic"], ["42", "gone"]]);
      expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:chat-image");
      expect(container.textContent).toContain("图片已失效");
      expect(container.querySelector('button[aria-label="翻译本条消息"]')).toBeNull();
    } finally {
      Object.assign(URL, { createObjectURL: original.create, revokeObjectURL: original.revoke });
    }
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
    const failedRetry = [...container.querySelectorAll("button")].find(button => button.textContent === "翻译失败，点击重试")!;
    for (const action of [container.querySelector<HTMLButtonElement>('button[aria-label="翻译本条消息"]')!, failedRetry]) {
      expect(action.disabled).toBe(true);
      await act(async () => action.click());
    }
    expect(onTranslate).not.toHaveBeenCalled();
  });

  describe("long-press actions", () => {
    const press = (target: Element, init: PointerEventInit = {}) =>
      target.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerType: "touch", clientX: 5, clientY: 5, ...init }));
    const sheetButtons = () => [...document.body.querySelectorAll(".ant-drawer button")].map((button) => button.textContent);

    beforeEach(() => {
      vi.useFakeTimers();
      Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn().mockResolvedValue(undefined) }, configurable: true });
    });
    afterEach(() => vi.useRealTimers());

    it("opens the message actions after a touch hold and runs the chosen one", async () => {
      const onTranslate = vi.fn();
      const onForkFromMessage = vi.fn();
      const target = message({ id: "b", content: "buyer text" });
      await render({ messages: [message({ id: "a", content: "first" }), target], onTranslate, onForkFromMessage, onOpenCard: vi.fn() });

      await act(async () => { press(container.querySelector('[data-message-id="b"] [data-message-text]')!); vi.advanceTimersByTime(500); });
      expect(sheetButtons()).toEqual(["翻译", "从此开始模拟", "复制"]);

      const choose = (label: string) => [...document.body.querySelectorAll(".ant-drawer button")].find((button) => button.textContent === label) as HTMLButtonElement;
      await act(async () => choose("复制").click());
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith("buyer text");
      await act(async () => { press(container.querySelector('[data-message-id="b"] [data-message-text]')!); vi.advanceTimersByTime(500); });
      await act(async () => choose("从此开始模拟").click());
      expect(onForkFromMessage).toHaveBeenCalledExactlyOnceWith(1);
      expect(onTranslate).not.toHaveBeenCalled();
    });

    it.each([
      ["a mouse press", { pointerType: "mouse" }, 500],
      ["a release before the hold time", {}, 300],
    ])("does not open the actions for %s", async (_name, init, wait) => {
      await render({ messages: [message({ id: "a" })], onTranslate: vi.fn(), onForkFromMessage: vi.fn(), onOpenCard: vi.fn() });
      const body = container.querySelector("[data-message-text]")!;
      await act(async () => {
        press(body, init);
        vi.advanceTimersByTime(wait);
        body.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
        vi.advanceTimersByTime(500);
      });
      expect(sheetButtons()).toEqual([]);
    });

    it("cancels the hold when the finger moves, as when scrolling", async () => {
      await render({ messages: [message({ id: "a" })], onForkFromMessage: vi.fn(), onOpenCard: vi.fn() });
      const body = container.querySelector("[data-message-text]")!;
      await act(async () => {
        press(body);
        body.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 5, clientY: 60 }));
        vi.advanceTimersByTime(600);
      });
      expect(sheetButtons()).toEqual([]);
    });
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
