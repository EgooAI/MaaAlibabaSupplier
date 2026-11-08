// @vitest-environment happy-dom
import { act, StrictMode, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UpdateCard } from "@/features/settings/UpdateCard";
import { ApiError } from "@/services/httpAdapter";
import type { UpdateState } from "@/types/update";

const mocks = vi.hoisted(() => ({
  auth: { phase: "authenticated", generation: 1 },
  backend: { getAppUpdate: vi.fn(), checkAppUpdate: vi.fn(), installAppUpdate: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("@/features/auth/AuthProvider", () => ({ useAuth: () => mocks.auth }));
vi.mock("antd", () => {
  const Group = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  return {
    Space: Group, Tag: Group,
    Tooltip: ({ title, children }: { title?: ReactNode; children?: ReactNode }) => <div>{title}{children}</div>,
    Typography: { Text: Group, Paragraph: Group, Link: ({ children, ...props }: { children: ReactNode; href: string }) => <a {...props}>{children}</a> },
    Card: ({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }) => <section>{title}{extra}{children}</section>,
    Alert: ({ title, description }: { title: ReactNode; description?: ReactNode }) => <aside>{title}{description}</aside>,
    Spin: () => <span>loading</span>,
    Progress: ({ percent }: { percent: number }) => <progress value={percent} max={100} />,
    Descriptions: Object.assign(({ title, items, children }: { title?: string; items?: { key: string; label: string; children: ReactNode }[]; children?: ReactNode }) => <div>{title}{items?.map((item) => <div key={item.key}>{item.label}: {item.children}</div>)}{children}</div>, { Item: Group }),
    Button: ({ children, disabled, loading, onClick, danger }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean; danger?: boolean }) => <button data-danger={Boolean(danger)} disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Modal: ({ open, title, children, onOk, onCancel, okText }: { open: boolean; title: string; children: ReactNode; onOk: () => void; onCancel: () => void; okText: string }) => open ? <div role="dialog">{title}{children}<button onClick={onOk}>{okText}</button><button onClick={onCancel}>取消</button></div> : null,
  };
});

const candidate = { id: "run/42:attempt/2", version: "v2", sha: "abcdef0123456789", run_id: 42, run_attempt: 2, created_at: "2026-09-18T10:00:00Z", url: "https://github.com/example/app/actions/runs/42" };
function update(overrides: Partial<UpdateState> = {}): UpdateState {
  return {
    supported: true, auto: true, reason: null, phase: "idle", current: { version: "v1", sha: "1234567" },
    source: { repository: "example/app", branch: "main", workflow: "build.yml", artifact: "windows-app" },
    candidate: null, downloaded_bytes: 0, total_bytes: null, error: null, last_result: null, ...overrides,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
let root: Root;
let container: HTMLDivElement;
const render = async () => { await act(async () => root.render(<UpdateCard />)); };
function button(text: string) {
  const found = [...container.querySelectorAll("button")].find((node) => node.textContent === text);
  if (!found) throw new Error(`Missing button: ${text}`);
  return found;
}
const click = async (text: string) => { await act(async () => button(text).click()); };
const advance = async (ms = 1500) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.auth = { phase: "authenticated", generation: 1 };
  mocks.backend.getAppUpdate.mockResolvedValue(update());
  mocks.backend.checkAppUpdate.mockResolvedValue(update({ phase: "checking" }));
  mocks.backend.installAppUpdate.mockResolvedValue({ accepted: true });
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

describe("application update", () => {
  it("reads local status once across Strict Mode replay, then every 30 seconds, and never checks remotely itself", async () => {
    await act(async () => root.render(<StrictMode><UpdateCard /></StrictMode>));
    await advance(29_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    await advance(1_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
    for (const text of ["v1", "1234567", "example/app", "main", "build.yml", "windows-app"]) expect(container.textContent).toContain(text);
  });

  it.each([[true, "每 5 分钟"], [false, "后台自动检查已关闭"]])("describes background checking when auto is %s", async (auto, text) => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ auto }));
    await render();
    expect(container.textContent).toContain(text);
  });

  it("allows retrying a failed initial local read without triggering a remote check", async () => {
    mocks.backend.getAppUpdate.mockRejectedValueOnce(new Error("backend offline"));
    await render();
    expect(container.textContent).toContain("backend offline");
    expect(button("检查更新").disabled).toBe(true);
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    await click("刷新本地状态");
    expect(container.textContent).toContain("v1");
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
  });

  it.each([
    ["null", null],
    ["missing current.version", { ...update(), current: { sha: null } }],
    ["nonnumeric progress", { ...update(), downloaded_bytes: "1024" }],
    ["missing auto", { ...update(), auto: undefined }],
  ])("recovers from an initial malformed snapshot: %s", async (_, value) => {
    mocks.backend.getAppUpdate.mockResolvedValueOnce(value);
    await render();
    expect(container.textContent).toContain("更新状态响应无效");
    expect(container.textContent).toContain("状态不可用");
    expect(container.textContent).not.toContain("loading");
    expect(button("检查更新").disabled).toBe(true);
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    await click("刷新本地状态");
    expect(container.textContent).toContain("v1");
    expect(container.textContent).not.toContain("更新状态响应无效");
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
  });

  it.each(["check", "poll"] as const)("preserves the last valid snapshot after malformed %s data", async (action) => {
    const valid = update({ phase: action === "poll" ? "downloading" : "ready", candidate });
    mocks.backend.getAppUpdate.mockResolvedValueOnce(valid);
    await render();
    if (action === "poll") {
      mocks.backend.getAppUpdate.mockResolvedValueOnce({ ...valid, source: null });
      await advance();
    } else {
      mocks.backend.checkAppUpdate.mockResolvedValueOnce({ ...valid, candidate: { ...candidate, run_id: "42" } });
      await click("检查更新");
    }
    expect(container.textContent).toContain("更新状态响应无效");
    expect(container.textContent).toContain("v1");
    expect(container.textContent).toContain("example/app");
    expect(container.textContent).toContain(candidate.sha);
    const reads = mocks.backend.getAppUpdate.mock.calls.length;
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(reads);
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "ready", candidate }));
    await click("刷新本地状态");
    expect(container.textContent).not.toContain("更新状态响应无效");
    expect(button("安装并立即重启").disabled).toBe(false);
  });

  it("polls every 1.5 seconds while checking or downloading, with no overlapping slow reads", async () => {
    await render();
    await click("检查更新");
    const read = deferred<UpdateState>();
    mocks.backend.getAppUpdate.mockReturnValueOnce(read.promise);
    await advance();
    await advance(30_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    await act(async () => read.resolve(update({ phase: "downloading", candidate, downloaded_bytes: 1048576, total_bytes: 4194304 })));
    expect(container.querySelector("progress")?.value).toBe(25);
    expect(container.textContent).toContain("1.0 MiB / 4.0 MiB");
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "ready", candidate }));
    await advance();
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(3);
    expect(button("安装并立即重启").disabled).toBe(false);
    await advance(29_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(3);
    await advance(1_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(4);
    expect(mocks.backend.checkAppUpdate).toHaveBeenCalledTimes(1);
  });

  it("keeps reading local state during restart and returns to normal after reconnecting", async () => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "installing", candidate }));
    await render();
    await advance();
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    mocks.backend.getAppUpdate.mockResolvedValue(update({ current: { version: "v2", sha: candidate.sha },
      last_result: { status: "installed", message: "helper completed", version: "v2", executed_at: 1794000000 } }));
    await advance();
    expect(container.textContent).toContain("最近一次安装已完成");
    expect(container.textContent).toContain("安装执行时间");
    expect(container.textContent).not.toContain("等待程序重启");
    expect(button("检查更新").disabled).toBe(false);
  });

  it("requires confirmation for the exact candidate and sends nothing on cancel", async () => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "ready", candidate }));
    await render();
    expect(container.textContent).toContain(candidate.created_at);
    expect(button("安装并立即重启").dataset.danger).toBe("false");
    await click("安装并立即重启");
    const dialog = container.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain(candidate.sha);
    expect(dialog.textContent).toContain("后台任务空闲时立即安装");
    expect(dialog.textContent).toContain("有任务运行或排队时，本次安装将被拒绝");
    await click("取消");
    expect(mocks.backend.installAppUpdate).not.toHaveBeenCalled();
    await click("安装并立即重启");
    await click("确认安装并重启");
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledExactlyOnceWith(candidate.id);
    expect(container.textContent).toContain("连接恢复后将自动显示最新状态");
    const reload = vi.spyOn(window.location, "reload").mockImplementation(() => {});
    await click("手动刷新并重新连接");
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("deduplicates rapid check and install clicks before a render", async () => {
    await render();
    const check = deferred<UpdateState>();
    mocks.backend.checkAppUpdate.mockReturnValueOnce(check.promise);
    await act(async () => { const target = button("检查更新"); target.click(); target.click(); });
    expect(mocks.backend.checkAppUpdate).toHaveBeenCalledTimes(1);
    await act(async () => check.resolve(update({ phase: "ready", candidate })));
    const install = deferred<{ accepted: true }>();
    mocks.backend.installAppUpdate.mockReturnValueOnce(install.promise);
    await click("安装并立即重启");
    await act(async () => { const target = button("确认安装并重启"); target.click(); target.click(); });
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledTimes(1);
    await act(async () => install.resolve({ accepted: true }));
  });

  it.each([
    new Error("connection lost"),
    new ApiError("API request failed", "/api/app/update/install", { cause: new TypeError("Failed to fetch") }),
    new ApiError("API response body is not valid JSON", "/api/app/update/install"),
  ])("treats an ambiguous install failure as uncertain without retrying: %s", async (error) => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "ready", candidate }));
    mocks.backend.installAppUpdate.mockRejectedValueOnce(error);
    await render();
    await click("安装并立即重启");
    await click("确认安装并重启");
    expect(container.textContent).toContain("无法确认安装请求是否已接受");
    expect(container.textContent).toContain("不要重复提交安装");
    expect(container.textContent).not.toContain("安装完成");
    expect(container.querySelectorAll("button")).toHaveLength(1);
    await advance(60_000);
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledTimes(1);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
  });

  it.each([409, 503])("shows an explicit HTTP %s install rejection in a dialog and permits a new manual attempt", async (status) => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "ready", candidate }));
    const message = `Installation rejected before handoff (${status})`;
    mocks.backend.installAppUpdate.mockRejectedValueOnce(new ApiError(message, "/api/app/update/install", { status }));
    await render();
    await click("安装并立即重启");
    await click("确认安装并重启");
    expect(container.querySelector('[role="dialog"]')?.textContent).toContain(message);
    expect(container.textContent).not.toContain("无法确认安装请求是否已接受");
    expect(container.textContent).not.toContain("手动刷新并重新连接");
    expect(container.textContent).toContain(candidate.sha);
    expect(button("安装并立即重启").disabled).toBe(false);
    await click("知道了");
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledTimes(1);
    const nextCandidate = { ...candidate, id: "refreshed-candidate" };
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "ready", candidate: nextCandidate }));
    await advance(30_000);
    expect(container.textContent).not.toContain(message);
    await click("安装并立即重启");
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledTimes(1);
    await click("确认安装并重启");
    expect(mocks.backend.installAppUpdate).toHaveBeenNthCalledWith(2, nextCandidate.id);
    expect(container.textContent).toContain("连接恢复后将自动显示最新状态");
  });

  it.each([null, {}, { accepted: false }, { accepted: "true" }, { accepted: 1 }])("requires literal acceptance and treats a malformed success receipt as uncertain: %j", async (receipt) => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "ready", candidate }));
    mocks.backend.installAppUpdate.mockResolvedValueOnce(receipt);
    await render();
    await click("安装并立即重启");
    await click("确认安装并重启");
    expect(container.textContent).toContain("无法确认安装请求是否已接受");
    expect(container.querySelectorAll("button")).toHaveLength(1);
    expect(button("手动刷新并重新连接").disabled).toBe(false);
    await advance(60_000);
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledTimes(1);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
  });

  it("shows a backend error state, hides installation, and recovers through local polling", async () => {
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "downloading", candidate }))
      .mockResolvedValueOnce(update({ phase: "error", candidate, error: "artifact expired", last_result: { status: "failed", message: "previous failure", version: "v0" } }));
    await render();
    await advance();
    expect(container.textContent).toContain("artifact expired");
    expect(container.textContent).toContain("previous failure");
    expect(container.textContent).not.toContain("安装并立即重启");
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "ready", candidate }));
    await advance(30_000);
    expect(button("安装并立即重启").disabled).toBe(false);
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
  });

  it("keeps the last state after a failed poll and resumes only through local status refresh", async () => {
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "checking", candidate }))
      .mockRejectedValueOnce(new Error("status offline"));
    await render();
    await advance();
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain(candidate.sha);
    expect(container.textContent).toContain("status offline");
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "ready", candidate: { ...candidate, id: "new-id", version: "v3" } }));
    await click("刷新本地状态");
    expect(container.textContent).toContain("v3");
    expect(container.textContent).not.toContain("status offline");
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
  });

  it("gates requests on auth readiness and discards responses from an old auth generation", async () => {
    mocks.auth.phase = "verifying";
    await render();
    expect(mocks.backend.getAppUpdate).not.toHaveBeenCalled();
    mocks.auth.phase = "authenticated";
    const old = deferred<UpdateState>();
    mocks.backend.getAppUpdate.mockReturnValueOnce(old.promise);
    await render();
    mocks.auth.generation++;
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ current: { version: "new-session", sha: null } }));
    await render();
    await act(async () => old.resolve(update({ phase: "downloading", candidate })));
    expect(container.textContent).toContain("new-session");
    expect(container.textContent).not.toContain(candidate.sha);
    await advance(29_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
  });

  it("clears polling and confirmation on auth teardown", async () => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "checking" }));
    await render();
    mocks.auth.phase = "signedOut";
    await render();
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    mocks.auth = { phase: "authenticated", generation: 2 };
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "ready", candidate }));
    await render();
    await click("安装并立即重启");
    mocks.auth.phase = "signedOut";
    await render();
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(mocks.backend.installAppUpdate).not.toHaveBeenCalled();
  });

  it("ignores pending actions after unmount and starts no further polling", async () => {
    await render();
    const old = deferred<UpdateState>();
    mocks.backend.checkAppUpdate.mockReturnValueOnce(old.promise);
    await click("检查更新");
    await act(async () => root.render(null));
    await act(async () => old.resolve(update({ phase: "checking" })));
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
  });

  it("explains unsupported source/dev builds and disables all update operations", async () => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ supported: false, reason: "源码运行不支持安装", phase: "ready", candidate }));
    await render();
    expect(container.textContent).toContain("源码运行不支持安装");
    expect(button("检查更新").disabled).toBe(true);
    expect(button("安装并立即重启").disabled).toBe(true);
    await click("检查更新");
    await click("安装并立即重启");
    await advance(60_000);
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
    expect(mocks.backend.installAppUpdate).not.toHaveBeenCalled();
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
  });
});
