// @vitest-environment happy-dom
import { act, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ClientLiveView } from "@/features/remote/ClientLiveView";

const mocks = vi.hoisted(() => ({ backend: {
  getRemoteActivity: vi.fn(), getRemoteFrame: vi.fn(), downloadRemoteOriginal: vi.fn(),
} }));
vi.mock("@/features/account/AccountProvider", () => ({ useAccountBackend: () => mocks.backend }));
// Uses a plain image to inspect blob URLs without Next.js optimization in tests.
// eslint-disable-next-line @next/next/no-img-element
vi.mock("next/image", () => ({ default: ({ src, alt }: { src: string; alt: string }) => <img src={src} alt={alt} /> }));
vi.mock("antd", () => ({
  Button: ({ icon, loading, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { icon?: ReactNode; loading?: boolean }) => <button {...props} disabled={props.disabled || loading}>{icon}{children}</button>,
  Popover: ({ open, title, content, children, onOpenChange }: { open: boolean; title: ReactNode; content: ReactNode; children: ReactNode; onOpenChange: (open: boolean) => void }) =>
    <div data-preview-trigger onMouseEnter={() => onOpenChange(true)} onMouseLeave={() => onOpenChange(false)}>{children}{open ? <>{title}{content}</> : null}</div>,
  Spin: () => <span>加载中</span>,
  Alert: ({ title }: { title: ReactNode }) => <div role="alert">{title}</div>,
}));

let root: Root;
let container: HTMLDivElement;
const idleActivity = { sending: false, navigating: false };
const frame = () => ({ blob: new Blob(["frame"], { type: "image/jpeg" }), etag: "frame-1", width: 1280, height: 720 });
const render = async (key = "account-a") => { await act(async () => root.render(<ClientLiveView key={key} />)); };
const advance = async (ms: number) => { await act(async () => vi.advanceTimersByTimeAsync(ms)); };
const toggle = async () => { await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="客户端实况"]')!.click()); };
const hover = async (enter: boolean) => { await act(async () => container.querySelector("[data-preview-trigger]")!.dispatchEvent(new MouseEvent(enter ? "mouseover" : "mouseout", { bubbles: true }))); };
const panel = () => container.querySelector('[aria-label="客户端实况小窗"]');
async function hidden(value: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, value });
  await act(async () => document.dispatchEvent(new Event("visibilitychange")));
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:live-frame");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  mocks.backend.getRemoteActivity.mockResolvedValue(idleActivity);
  mocks.backend.getRemoteFrame.mockResolvedValue(frame());
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("requests frames only while hovered or pinned, and stops transfers in a background tab", async () => {
  await render();
  await advance(1000);
  expect(mocks.backend.getRemoteFrame).not.toHaveBeenCalled();
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(2);
  await hover(true);
  expect(panel()).not.toBeNull();
  expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:live-frame");
  await toggle();
  await hover(false);
  expect(panel()).not.toBeNull();
  const activityRequests = mocks.backend.getRemoteActivity.mock.calls.length;
  await advance(2000);
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(activityRequests);
  await hidden(true);
  expect(panel()).toBeNull();
  const requests = mocks.backend.getRemoteFrame.mock.calls.length;
  expect(mocks.backend.getRemoteFrame.mock.calls[0][1].aborted).toBe(true);
  await advance(2000);
  expect(mocks.backend.getRemoteFrame).toHaveBeenCalledTimes(requests);
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(activityRequests);
  await hidden(false);
  expect(panel()).not.toBeNull();
  await toggle();
  expect(panel()).toBeNull();
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(activityRequests + 1);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:live-frame");
  await hidden(true);
  mocks.backend.getRemoteActivity.mockResolvedValue({ ...idleActivity, sending: true });
  await advance(2000);
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(activityRequests + 1);
  expect(panel()).toBeNull();
  await hidden(false);
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(activityRequests + 2);
  expect(panel()).not.toBeNull();
});

it.each([
  { activity: { sending: true, navigating: false }, label: "正在发送消息" },
  { activity: { sending: false, navigating: true }, label: "正在跳转联系人" },
])("opens for $label, closes at completion, and preserves an explicitly pinned view", async ({ activity, label }) => {
  await render();
  mocks.backend.getRemoteActivity.mockResolvedValue(activity);
  await advance(999);
  expect(panel()).toBeNull();
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(1);
  await advance(1);
  expect(panel()).not.toBeNull();
  expect(container.textContent).toContain(label);
  expect(mocks.backend.getRemoteFrame).toHaveBeenCalled();
  mocks.backend.getRemoteActivity.mockResolvedValue(idleActivity);
  await advance(1000);
  expect(panel()).toBeNull();
  const frames = mocks.backend.getRemoteFrame.mock.calls.length;
  await advance(1000);
  expect(mocks.backend.getRemoteFrame).toHaveBeenCalledTimes(frames);
  await toggle();
  mocks.backend.getRemoteActivity.mockResolvedValue(activity);
  await advance(1000);
  mocks.backend.getRemoteActivity.mockResolvedValue(idleActivity);
  await advance(1000);
  expect(panel()).not.toBeNull();
  await toggle();
  expect(panel()).toBeNull();
});

it.each([
  { sending: true, navigating: false },
  { sending: false, navigating: true },
])("discards late activity (sending=$sending, navigating=$navigating) after pinning without overlapping reads", async (activity) => {
  let finish!: (activity: typeof idleActivity) => void;
  mocks.backend.getRemoteActivity.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
  await render();
  await advance(5000);
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(1);
  await toggle();
  await act(async () => finish(activity));
  await advance(2000);
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(1);
  mocks.backend.getRemoteActivity.mockReturnValue(new Promise(() => {}));
  await toggle();
  expect(mocks.backend.getRemoteActivity).toHaveBeenCalledTimes(2);
  expect(panel()).toBeNull();
});

it("measures integer sample FPS including unchanged frames and returns to zero on stalled capture", async () => {
  await render();
  await toggle();
  mocks.backend.getRemoteFrame.mockResolvedValue({ ...frame(), blob: null });
  await advance(1000);
  expect(container.textContent).toContain("5 FPS");
  expect(mocks.backend.getRemoteFrame.mock.calls[1][0]).toBe("frame-1");
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  mocks.backend.getRemoteFrame.mockReturnValue(new Promise(() => {}));
  await advance(2000);
  expect(container.textContent).toContain("0 FPS");
});

it("discards a late frame after closing or changing accounts and never starts overlapping frame reads", async () => {
  let finish!: (result: ReturnType<typeof frame>) => void;
  mocks.backend.getRemoteFrame.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
  await render();
  await toggle();
  await advance(1000);
  expect(mocks.backend.getRemoteFrame).toHaveBeenCalledTimes(1);
  await render("account-b");
  expect(mocks.backend.getRemoteFrame.mock.calls[0][1].aborted).toBe(true);
  await act(async () => finish(frame()));
  expect(panel()).toBeNull();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  await advance(1000);
  expect(mocks.backend.getRemoteFrame).toHaveBeenCalledTimes(1);
});
