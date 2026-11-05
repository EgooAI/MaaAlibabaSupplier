// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComposeTranslator } from "@/features/chat/workspace/ComposeTranslator";

const mocks = vi.hoisted(() => ({ composeTranslation: vi.fn() }));
vi.mock("@/features/account/AccountProvider", () => ({
  useAccountBackend: () => mocks,
  AccountChangedError: class AccountChangedError extends Error {},
}));

let root: Root;
let container: HTMLDivElement;

function Harness({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial);
  return <><output data-draft>{value}</output><ComposeTranslator conversationId="42" draft={value} onDraftChange={setValue} /></>;
}

const draft = () => container.querySelector("[data-draft]")!.textContent;

const button = (name: string) => [...container.querySelectorAll("button")].find((item) => item.textContent?.includes(name));
async function type(text: string) {
  const input = container.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  mocks.composeTranslation.mockReset();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ComposeTranslator", () => {
  it("replaces the draft with the translation and undoes back to the original draft", async () => {
    mocks.composeTranslation.mockResolvedValueOnce({ translation: "First" }).mockResolvedValueOnce({ translation: "Second" });
    await act(async () => root.render(<Harness initial="my draft" />));
    expect(button("翻译")!.disabled).toBe(true);

    await type(" 你好 ");
    await act(async () => button("翻译")!.click());
    expect(mocks.composeTranslation).toHaveBeenLastCalledWith("42", "你好");
    expect(draft()).toBe("First");

    await act(async () => button("翻译")!.click());
    expect(draft()).toBe("Second");
    await act(async () => button("撤销")!.click());
    expect(draft()).toBe("my draft");
    expect(button("撤销")).toBeUndefined();
  });

  it("offers no undo once the draft was edited after the translation", async () => {
    mocks.composeTranslation.mockResolvedValueOnce({ translation: "First" });
    await act(async () => root.render(<Harness initial="" />));
    await type("你好");
    await act(async () => button("翻译")!.click());
    expect(button("撤销")).toBeDefined();

    await act(async () => root.render(<ComposeTranslator conversationId="42" draft="First, edited" onDraftChange={() => undefined} />));
    expect(button("撤销")).toBeUndefined();
  });

  it("shows the failure and keeps the draft", async () => {
    mocks.composeTranslation.mockRejectedValueOnce(new Error("AI 暂不可用"));
    await act(async () => root.render(<Harness initial="keep me" />));
    await type("你好");
    await act(async () => button("翻译")!.click());
    expect(container.querySelector('[role="alert"]')?.textContent).toBe("AI 暂不可用");
    expect(draft()).toBe("keep me");
  });

  it("ignores a result that arrives after the conversation was left", async () => {
    let resolve!: (value: { translation: string }) => void;
    mocks.composeTranslation.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const onDraftChange = vi.fn();
    await act(async () => root.render(<ComposeTranslator conversationId="42" draft="" onDraftChange={onDraftChange} />));
    await type("你好");
    await act(async () => button("翻译")!.click());

    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => resolve({ translation: "late" }));
    expect(onDraftChange).not.toHaveBeenCalled();
  });
});
