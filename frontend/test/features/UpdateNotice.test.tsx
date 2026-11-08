// @vitest-environment happy-dom
import { act, StrictMode, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { UpdateNotice } from "@/features/settings/UpdateNotice";
import { formatFullDateTime } from "@/domain/time";
import type { InstallationNotice } from "@/types/update";

const mocks = vi.hoisted(() => ({ getInstallationNotice: vi.fn(), acknowledgeInstallation: vi.fn() }));
vi.mock("@/services/client", () => ({ backend: mocks }));
vi.mock("antd", () => {
  const Group = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  return {
    Space: Group, Typography: { Text: Group },
    Card: ({ title, children }: { title: string; children: ReactNode }) => <section>{title}{children}</section>,
    Alert: ({ title }: { title: string }) => <aside>{title}</aside>,
    Button: ({ loading, onClick, children }: { loading: boolean; onClick: () => void; children: ReactNode }) => <button disabled={loading} onClick={onClick}>{children}</button>,
  };
});

const notice: InstallationNotice = { id: "a".repeat(32), version: "v2", executed_at: 1794000000, completed_at: 1794000120 };
let root: Root;
let container: HTMLDivElement;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
const render = async (content: ReactNode = <UpdateNotice />) => { await act(async () => root.render(content)); };
const advance = async (ms = 5000) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const acknowledge = async () => { await act(async () => container.querySelector("button")!.click()); };

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.getInstallationNotice.mockResolvedValue(notice);
  mocks.acknowledgeInstallation.mockResolvedValue(null);
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

it("keeps the global notice until acknowledgment and synchronizes another device by polling", async () => {
  await render(<StrictMode><UpdateNotice /><UpdateNotice /></StrictMode>);
  expect(mocks.getInstallationNotice).toHaveBeenCalledTimes(2);
  expect(container.querySelectorAll("button")).toHaveLength(2);
  expect(container.textContent).toContain(formatFullDateTime(notice.executed_at));
  await advance(60000);
  expect(container.querySelectorAll("button")).toHaveLength(2);
  await acknowledge();
  expect(mocks.acknowledgeInstallation).toHaveBeenCalledExactlyOnceWith(notice.id);
  expect(container.querySelectorAll("button")).toHaveLength(1);
  mocks.getInstallationNotice.mockResolvedValue(null);
  await advance();
  expect(container.textContent).toBe("");
});

it("does not let a pre-acknowledgment read restore the dismissed notice", async () => {
  await render();
  const old = deferred<InstallationNotice | null>();
  mocks.getInstallationNotice.mockReturnValueOnce(old.promise);
  await advance();
  const save = deferred<InstallationNotice | null>();
  mocks.acknowledgeInstallation.mockReturnValueOnce(save.promise);
  await acknowledge();
  await acknowledge();
  expect(mocks.acknowledgeInstallation).toHaveBeenCalledTimes(1);
  await act(async () => save.resolve(null));
  await act(async () => old.resolve(notice));
  expect(container.textContent).toBe("");
});

it("retains the notice through read and acknowledgment failures and only dismisses after server success", async () => {
  await render();
  mocks.getInstallationNotice.mockRejectedValueOnce(new Error("offline"));
  await advance();
  expect(container.textContent).toContain(notice.version);
  mocks.acknowledgeInstallation.mockRejectedValueOnce(new Error("确认未保存"));
  await acknowledge();
  expect(container.textContent).toContain("确认未保存");
  expect(container.querySelector("button")?.disabled).toBe(false);
  await acknowledge();
  expect(container.textContent).toBe("");
});

it("shows a newer installation returned when an old notice was acknowledged", async () => {
  await render();
  const next = { ...notice, id: "b".repeat(32), version: "v3", executed_at: notice.executed_at + 300, completed_at: notice.completed_at + 300 };
  mocks.acknowledgeInstallation.mockResolvedValueOnce(next);
  await acknowledge();
  expect(container.textContent).toContain("v3");
  expect(container.textContent).toContain(formatFullDateTime(next.executed_at));
  expect(container.querySelector("button")).not.toBeNull();
});

it("rejects malformed notices and discards pending responses after auth teardown", async () => {
  mocks.getInstallationNotice.mockResolvedValueOnce({ ...notice, executed_at: "bad" });
  await render();
  expect(container.textContent).toBe("");
  const old = deferred<InstallationNotice | null>();
  mocks.getInstallationNotice.mockReturnValueOnce(old.promise);
  await advance();
  await render(null);
  await act(async () => old.resolve(notice));
  await advance(60000);
  expect(mocks.getInstallationNotice).toHaveBeenCalledTimes(2);
  expect(container.textContent).toBe("");
});
