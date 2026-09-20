// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranslationToolbar } from "@/features/chat/conversation/TranslationToolbar";

let root: Root;
let container: HTMLDivElement;

const render = async (props: Partial<Parameters<typeof TranslationToolbar>[0]>) => {
  await act(async () => root.render(
    <TranslationToolbar
      visible={true}
      onToggleVisible={vi.fn()}
      translatedCount={0}
      untranslatedCount={0}
      pendingCount={0}
      canTranslate={true}
      onTranslateMissing={vi.fn()}
      onRetranslateAll={vi.fn()}
      {...props}
    />,
  ));
};

const buttonOf = (label: string) => [...container.querySelectorAll("button")].find(button => button.textContent?.includes(label));

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

describe("translation toolbar", () => {
  it("hides the missing-translation button and stops counting once nothing is missing", async () => {
    await render({ translatedCount: 5, untranslatedCount: 0 });
    expect(container.textContent).toContain("译文 5/5");
    expect(buttonOf("翻译缺失")).toBeUndefined();
  });

  it.each([
    { reason: "a job is in flight", pendingCount: 2, canTranslate: true },
    { reason: "translation capability is unavailable", pendingCount: 0, canTranslate: false },
  ])("disables actions when $reason", async ({ pendingCount, canTranslate }) => {
    const onTranslateMissing = vi.fn();
    const onRetranslateAll = vi.fn();
    await render({ translatedCount: 2, untranslatedCount: 3, pendingCount, canTranslate, onTranslateMissing, onRetranslateAll });
    if (pendingCount) expect(container.textContent).toContain("翻译中 2 条");
    expect(buttonOf("翻译缺失")?.disabled).toBe(true);
    expect(buttonOf("重新翻译全部")?.disabled).toBe(true);
    await act(async () => { buttonOf("翻译缺失")!.click(); buttonOf("重新翻译全部")!.click(); });
    expect(onTranslateMissing).not.toHaveBeenCalled();
    expect(onRetranslateAll).not.toHaveBeenCalled();
  });

  it("invokes onTranslateMissing when clicking the missing-translation button", async () => {
    const onTranslateMissing = vi.fn();
    await render({ translatedCount: 2, untranslatedCount: 3, onTranslateMissing });
    expect(container.textContent).toContain("译文 2/5");
    expect(buttonOf("翻译缺失")?.textContent).toContain("翻译缺失 (3)");
    await act(async () => buttonOf("翻译缺失")!.click());
    expect(onTranslateMissing).toHaveBeenCalledTimes(1);
  });
});
