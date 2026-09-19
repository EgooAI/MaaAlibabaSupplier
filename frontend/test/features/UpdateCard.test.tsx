// @vitest-environment happy-dom
import { act, StrictMode, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UpdateCard } from "@/features/settings/UpdateCard";
import { ApiError } from "@/services/httpAdapter";
import type { UpdateState } from "@/types/update";

const mocks = vi.hoisted(() => ({
  auth: { phase: "authenticated", generation: 1 },
  backend: { getAppUpdate: vi.fn(), checkAppUpdate: vi.fn(), downloadAppUpdate: vi.fn(), installAppUpdate: vi.fn() },
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
    Descriptions: Object.assign(({ title, items, children }: { title?: string; items?: { key: string; label: string; children: ReactNode }[]; children?: ReactNode }) => <div>{title}{items?.map((item) => <p key={item.key}>{item.label}: {item.children}</p>)}{children}</div>, { Item: Group }),
    Button: ({ children, disabled, loading, onClick }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Modal: ({ open, title, children, onOk, onCancel, okText }: { open: boolean; title: string; children: ReactNode; onOk: () => void; onCancel: () => void; okText: string }) => open ? <div role="dialog">{title}{children}<button onClick={onOk}>{okText}</button><button onClick={onCancel}>取消</button></div> : null,
  };
});

const candidate = { id: "run/42:attempt/2", version: "v2", sha: "abcdef0123456789", run_id: 42, run_attempt: 2, created_at: "2026-09-18T10:00:00Z", url: "https://github.com/example/app/actions/runs/42" };
function update(overrides: Partial<UpdateState> = {}): UpdateState {
  return {
    supported: true, reason: null, phase: "idle", current: { version: "v1", sha: "1234567" },
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
  mocks.backend.downloadAppUpdate.mockResolvedValue(update({ phase: "downloading", candidate }));
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

describe("manual application update", () => {
  it("reads local status once without an account provider and never checks remotely on mount or a timer", async () => {
    await render();
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
    expect(mocks.backend.downloadAppUpdate).not.toHaveBeenCalled();
    for (const text of ["v1", "1234567", "example/app", "main", "build.yml", "windows-app"]) expect(container.textContent).toContain(text);
  });

  it("deduplicates the initial read across Strict Mode effect replay", async () => {
    await act(async () => root.render(<StrictMode><UpdateCard /></StrictMode>));
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
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

  it.each(["check", "download", "poll"] as const)("preserves the last valid snapshot after malformed %s data", async (action) => {
    const valid = update({ phase: action === "poll" ? "downloading" : "available", candidate });
    mocks.backend.getAppUpdate.mockResolvedValueOnce(valid);
    await render();
    if (action === "poll") {
      mocks.backend.getAppUpdate.mockResolvedValueOnce({ ...valid, source: null });
      await advance();
    } else {
      mocks.backend[action === "check" ? "checkAppUpdate" : "downloadAppUpdate"].mockResolvedValueOnce({ ...valid, candidate: { ...candidate, run_id: "42" } });
      await click(action === "check" ? "检查更新" : "下载更新");
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

  it("polls only active checking/download phases, with no overlapping slow reads", async () => {
    await render();
    await click("检查更新");
    const read = deferred<UpdateState>();
    mocks.backend.getAppUpdate.mockReturnValueOnce(read.promise);
    await advance();
    await advance(30_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    await act(async () => read.resolve(update({ phase: "available", candidate })));
    await advance(30_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    await click("下载更新");
    expect(mocks.backend.downloadAppUpdate).toHaveBeenCalledExactlyOnceWith(candidate.id);
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "downloading", candidate, downloaded_bytes: 1048576, total_bytes: 4194304 }));
    await advance();
    expect(container.querySelector("progress")?.value).toBe(25);
    expect(container.textContent).toContain("1.0 MiB / 4.0 MiB");
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "ready", candidate }));
    await advance();
    await advance(30_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(4);
    expect(mocks.backend.checkAppUpdate).toHaveBeenCalledTimes(1);
  });

  it.each(["available", "ready", "error", "installing"] as const)("does not poll an initial %s state", async (phase) => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase, candidate }));
    await render();
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    expect(mocks.backend.checkAppUpdate).not.toHaveBeenCalled();
  });

  it("requires confirmation for the exact candidate and sends nothing on cancel", async () => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "ready", candidate }));
    await render();
    expect(container.textContent).toContain(candidate.created_at);
    await click("安装并立即重启");
    const dialog = container.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain(candidate.sha);
    expect(dialog.textContent).toContain("立即中断");
    expect(dialog.textContent).toContain("结果可能未知");
    expect(dialog.textContent).toContain("不要直接重试");
    await click("取消");
    expect(mocks.backend.installAppUpdate).not.toHaveBeenCalled();
    await click("安装并立即重启");
    await click("确认安装并重启");
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledExactlyOnceWith(candidate.id);
    expect(container.textContent).toContain("尚未确认安装完成");
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    const reload = vi.spyOn(window.location, "reload").mockImplementation(() => {});
    await click("手动刷新并重新连接");
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("deduplicates rapid check, download, and install clicks before a render", async () => {
    await render();
    const check = deferred<UpdateState>();
    mocks.backend.checkAppUpdate.mockReturnValueOnce(check.promise);
    await act(async () => { const target = button("检查更新"); target.click(); target.click(); });
    expect(mocks.backend.checkAppUpdate).toHaveBeenCalledTimes(1);
    await act(async () => check.resolve(update({ phase: "available", candidate })));
    const download = deferred<UpdateState>();
    mocks.backend.downloadAppUpdate.mockReturnValueOnce(download.promise);
    await act(async () => { const target = button("下载更新"); target.click(); target.click(); });
    expect(mocks.backend.downloadAppUpdate).toHaveBeenCalledTimes(1);
    await act(async () => download.resolve(update({ phase: "ready", candidate })));
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

  it.each([409, 503])("shows an explicit HTTP %s install rejection and permits a confirmed retry after local refresh", async (status) => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "ready", candidate }));
    const message = `Installation rejected before handoff (${status})`;
    mocks.backend.installAppUpdate.mockRejectedValueOnce(new ApiError(message, "/api/app/update/install", { status }));
    await render();
    await click("安装并立即重启");
    await click("确认安装并重启");
    expect(container.textContent).toContain(message);
    expect(container.textContent).not.toContain("无法确认安装请求是否已接受");
    expect(container.textContent).not.toContain("手动刷新并重新连接");
    expect(container.textContent).toContain(candidate.sha);
    expect(button("安装并立即重启").disabled).toBe(true);
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(1);
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledTimes(1);
    const nextCandidate = { ...candidate, id: "refreshed-candidate" };
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "ready", candidate: nextCandidate }));
    await click("刷新本地状态");
    expect(container.textContent).not.toContain(message);
    await click("安装并立即重启");
    expect(mocks.backend.installAppUpdate).toHaveBeenCalledTimes(1);
    await click("确认安装并重启");
    expect(mocks.backend.installAppUpdate).toHaveBeenNthCalledWith(2, nextCandidate.id);
    expect(container.textContent).toContain("尚未确认安装完成");
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

  it("preserves the candidate on a download failure and lets the user retry", async () => {
    mocks.backend.getAppUpdate.mockResolvedValue(update({ phase: "available", candidate }));
    mocks.backend.downloadAppUpdate.mockRejectedValueOnce(new Error("download offline"));
    await render();
    await click("下载更新");
    expect(container.textContent).toContain("download offline");
    expect(container.textContent).toContain(candidate.sha);
    await click("重试下载");
    expect(mocks.backend.downloadAppUpdate).toHaveBeenNthCalledWith(2, candidate.id);
    expect(container.textContent).toContain("总大小未知");
  });

  it("honors backend error state and stops polling until an explicit retry", async () => {
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "downloading", candidate }))
      .mockResolvedValueOnce(update({ phase: "error", candidate, error: "artifact expired", last_result: { status: "failed", message: "previous failure", version: "v0" } }));
    await render();
    await advance();
    await advance(60_000);
    expect(mocks.backend.getAppUpdate).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain("artifact expired");
    expect(container.textContent).toContain("previous failure");
    expect(container.textContent).not.toContain("安装并立即重启");
    await click("重试下载");
    expect(mocks.backend.downloadAppUpdate).toHaveBeenCalledExactlyOnceWith(candidate.id);
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
    mocks.backend.getAppUpdate.mockResolvedValueOnce(update({ phase: "available", candidate: { ...candidate, id: "new-id", version: "v3" } }));
    await click("刷新本地状态");
    await click("下载更新");
    expect(mocks.backend.downloadAppUpdate).toHaveBeenCalledExactlyOnceWith("new-id");
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
    await advance(60_000);
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
