// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MessageComposer } from "@/components/MessageComposer";

// antd Modal relies on portal + motion; happy-dom cannot unmount it after the
// leave animation. Replace only Modal with a static inline double; every other
// antd component stays real.
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>();
  type ModalDoubleProps = {
    open?: boolean;
    title?: React.ReactNode;
    okText?: string;
    cancelText?: string;
    onOk?: () => void;
    onCancel?: () => void;
    children?: React.ReactNode;
  };
  const Modal = ({ open, title, okText, cancelText, onOk, onCancel, children }: ModalDoubleProps) =>
    open ? (
      <div className="ant-modal-root">
        <div className="ant-modal">
          <div className="ant-modal-title">{title}</div>
          <div className="ant-modal-content">{children}</div>
          <div className="ant-modal-footer">
            <button type="button" onClick={onCancel}>{cancelText ?? "取消"}</button>
            <button type="button" onClick={onOk}>{okText ?? "确定"}</button>
          </div>
        </div>
      </div>
    ) : null;
  return { ...actual, Modal };
});

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
const modalTextarea = () => container.querySelector('textarea[aria-label="消息内容（展开编辑）"]') as HTMLTextAreaElement | null;
const modalButton = (pattern: RegExp) =>
  [...container.querySelectorAll(".ant-modal-footer button")].find(
    (button) => pattern.test(button.textContent ?? ""),
  ) as HTMLButtonElement | undefined;

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
  it("offers the expand affordance only in compact mode", async () => {
    await render({ compact: true });
    expect(expandButton()).not.toBeNull();
    await render({ compact: false });
    expect(expandButton()).toBeNull();
  });

  it("expands into a modal seeded with the draft and commits on confirm", async () => {
    const onChange = vi.fn();
    await render({ compact: true, value: "短回复", onChange });
    await act(async () => {
      expandButton()!.click();
    });
    expect(modalTextarea()?.value).toBe("短回复");
    await act(async () => {
      typeInto(modalTextarea()!, "第一行\n第二行长文本");
    });
    await act(async () => {
      modalButton(/完\s*成/)!.click();
    });
    expect(onChange).toHaveBeenLastCalledWith("第一行\n第二行长文本");
    expect(modalTextarea()).toBeNull();
  });

  it("discards modal edits when cancelled", async () => {
    const onChange = vi.fn();
    await render({ compact: true, value: "原草稿", onChange });
    await act(async () => {
      expandButton()!.click();
    });
    await act(async () => {
      typeInto(modalTextarea()!, "不该提交的修改");
    });
    await act(async () => {
      modalButton(/取\s*消/)!.click();
    });
    expect(onChange).not.toHaveBeenCalled();
    expect(modalTextarea()).toBeNull();
  });
});
