// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { MessageImage } from "@/features/chat/conversation/MessageImage";
import type { ChatMessage } from "@/types/chatCanonical";
import { ApiError } from "@/services/httpAdapter";

vi.mock("antd", () => ({ Image: ({ src }: { src: string }) => <span data-image={src} />, Spin: () => <span>loading</span> }));
let root: Root;
let container: HTMLDivElement;
const message = (id: string) => ({ id, externalMid: id, image: { width: 20, height: 10 } }) as ChatMessage;
const deferred = () => { let resolve!: (blob: Blob) => void; const promise = new Promise<Blob>((done) => { resolve = done; }); return { resolve, promise }; };
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.spyOn(URL, "createObjectURL").mockReturnValueOnce("blob:first").mockReturnValue("blob:next");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("releases the previous image and discards a late response after message replacement", async () => {
  const slow = deferred();
  const newest = deferred();
  const load = vi.fn().mockResolvedValueOnce(new Blob()).mockReturnValueOnce(slow.promise).mockReturnValueOnce(newest.promise);
  await act(async () => root.render(<MessageImage conversationId="1" message={message("first")} load={load} />));
  expect(container.querySelector("[data-image]")?.getAttribute("data-image")).toBe("blob:first");
  await act(async () => root.render(<MessageImage conversationId="1" message={message("slow")} load={load} />));
  expect(container.querySelector("[data-image]")).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:first");
  await act(async () => root.render(<MessageImage conversationId="2" message={message("newest")} load={load} />));
  await act(async () => slow.resolve(new Blob()));
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  await act(async () => newest.resolve(new Blob()));
  expect(container.querySelector("[data-image]")?.getAttribute("data-image")).toBe("blob:next");
});

it("retries a temporary archive failure and displays the completed response", async () => {
  const load = vi.fn().mockRejectedValueOnce(new ApiError("temporary", "image", { status: 502 })).mockResolvedValue(new Blob());
  await act(async () => root.render(<MessageImage conversationId="1" message={message("one")} load={load} />));
  expect(container.textContent).toContain("图片加载失败");
  await act(async () => container.querySelector("button")!.click());
  expect(container.querySelector("[data-image]")).not.toBeNull();
  expect(load).toHaveBeenCalledTimes(2);
});
