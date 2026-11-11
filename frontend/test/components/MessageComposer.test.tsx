// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessageComposer } from "@/components/MessageComposer";

let root: Root;
let container: HTMLDivElement;

const render = async (props: Partial<Parameters<typeof MessageComposer>[0]> = {}) => {
  await act(async () => {
    root.render(
      <MessageComposer
        value=""
        onChange={() => {}}
        onSend={() => {}}
        {...props}
      />,
    );
  });
};

const expandButton = () => container.querySelector('button[aria-label="展开编辑长文本"]') as HTMLButtonElement | null;
const collapseButton = () => container.querySelector('button[aria-label="收起长文本编辑"]') as HTMLButtonElement | null;

const typeInto = (textarea: HTMLTextAreaElement, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  setter?.call(textarea, value);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
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

describe("message composer", () => {
  it("sends directly and exposes fill-only as a separate dropdown action", async () => {
    const onSend = vi.fn();
    const onFill = vi.fn();
    await render({ compact: true, value: "reply", onSend, onFill });
    const send = [...container.querySelectorAll("button")].find((button) => button.textContent?.replaceAll(" ", "") === "发送")!;
    await act(async () => send.click());
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onFill).not.toHaveBeenCalled();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    await act(async () => (container.querySelector('[aria-label="更多发送方式"]') as HTMLButtonElement).click());
    const item = await vi.waitFor(() => {
      const option = [...document.querySelectorAll('[role="menuitem"]')].find((node) => node.textContent === "仅填入客户端");
      expect(option).toBeDefined();
      return option as HTMLElement;
    });
    await act(async () => item.click());
    expect(onFill).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("offers the expand affordance only in compact mode", async () => {
    await render({ compact: true });
    expect(expandButton()).not.toBeNull();
    await render({ compact: false });
    expect(expandButton()).toBeNull();
  });

  it("edits the live draft inline and preserves the editor and selection across toggles", async () => {
    const onChange = vi.fn();
    const onSend = vi.fn();
    const props = { compact: true, value: "短回复", onChange, onSend };
    await render(props);
    const textarea = container.querySelector("textarea")!;
    textarea.focus();
    textarea.setSelectionRange(1, 2);
    await act(async () => {
      expandButton()!.click();
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(container.querySelectorAll("textarea")).toHaveLength(1);
    expect(container.querySelector("textarea")).toBe(textarea);
    expect(document.activeElement).toBe(textarea);
    expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([1, 2]);
    expect(collapseButton()?.getAttribute("aria-expanded")).toBe("true");
    await act(async () => {
      typeInto(textarea, "第一行\n第二行长文本");
    });
    expect(onChange).toHaveBeenCalledExactlyOnceWith("第一行\n第二行长文本");
    await render({ ...props, value: "第一行\n第二行长文本" });
    await act(async () => {
      textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);

    // A replacement from translation or another draft source must remain authoritative.
    await render({ ...props, value: "更新后的草稿" });
    textarea.setSelectionRange(2, 4);
    await act(async () => {
      collapseButton()!.click();
    });
    expect(container.querySelector("textarea")).toBe(textarea);
    expect(textarea.value).toBe("更新后的草稿");
    expect(document.activeElement).toBe(textarea);
    expect([textarea.selectionStart, textarea.selectionEnd]).toEqual([2, 4]);
    expect(expandButton()?.getAttribute("aria-expanded")).toBe("false");
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});
