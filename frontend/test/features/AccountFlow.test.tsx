// @vitest-environment happy-dom
import { act, type ReactNode, type ButtonHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider } from "@/features/account/AccountProvider";
import { ClientCard, ConnectionFlowCard, SyncCard } from "@/features/account/AccountFlow";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { accountSession } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import type { ConnectionSnapshot } from "@/types/connection";
import { authenticatedSession } from "@/test/support/authFixture";

const mocks = vi.hoisted(() => ({
  backend: { getConnection: vi.fn(), connectClient: vi.fn(), retryConnection: vi.fn() },
  message: { error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => {
  const Text = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  return {
    App: { useApp: () => ({ message: mocks.message }) },
    Space: Text, Tag: Text, Typography: { Text },
    Card: ({ title, extra, children }: { title: string; extra?: ReactNode; children: ReactNode }) => <section aria-label={title}>{extra}{children}</section>,
    Tooltip: Text,
    Alert: ({ title, description, type }: { title: ReactNode; description?: ReactNode; type: string }) => <div role="alert" data-type={type}>{title}{description}</div>,
    Button: ({ children, disabled, loading, onClick, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => <button aria-label={props["aria-label"]} disabled={disabled || loading} onClick={onClick}>{children}</button>,
    Steps: ({ items }: { items: Array<{ key: string; title: string; content: string; status: string }> }) => <ol>{items.map((item) => <li key={item.key} data-step={item.key} data-status={item.status}>{item.title}：{item.content}</li>)}</ol>,
  };
});

let root: Root;
let container: HTMLDivElement;
let snapshot: ConnectionSnapshot;

beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  localStorage.clear();
  await authenticatedSession();
  accountSession.clear();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  snapshot = structuredClone(connectionSnapshot);
  mocks.backend.getConnection.mockImplementation(async () => structuredClone(snapshot));
  mocks.backend.retryConnection.mockImplementation(async () => structuredClone(snapshot));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const mount = async () => { await act(async () => root.render(<AccountProvider><ConnectionFlowCard /><SyncCard /><ClientCard /><DataDirBanner /></AccountProvider>)); };
const poll = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(10_000); }); };
const step = (key: string) => container.querySelector(`[data-step="${key}"]`)!;
const syncButton = () => container.querySelector<HTMLButtonElement>('section[aria-label="密钥与聊天同步"] button')!;

describe("account connection flow", () => {
  it("finishes client access after connecting without a seller confirmation action", async () => {
    mocks.backend.connectClient.mockImplementation(async () => {
      snapshot.client = { connected: true, window_generation: "window-a", detail: "客户端窗口已连接" };
      snapshot.capabilities.operate_client = true;
      return structuredClone(snapshot);
    });
    await mount();
    expect(container.textContent).toContain("只读模式：客户端尚未接入");
    const button = container.querySelector<HTMLButtonElement>('section[aria-label="客户端接入"] button')!;
    await act(async () => button.click());
    expect(mocks.backend.connectClient).toHaveBeenCalledExactlyOnceWith(snapshot.account.epoch);
    expect(step("client").getAttribute("data-status")).toBe("finish");
    expect(step("client").textContent).toContain("客户端接入");
    expect(accountSession.get().snapshot?.capabilities.operate_client).toBe(true);
    expect(container.querySelectorAll('section[aria-label="客户端接入"] button')).toHaveLength(1);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(container.textContent).not.toContain("人工确认");
    expect(container.textContent).not.toContain("只读模式");
    expect(container.textContent).toContain("截图并确认联系人与内容");
  });

  it("observes verifying, valid and committed ready states without submitting a retry", async () => {
    Object.assign(snapshot.source, { key_validation: "verifying", phase: "idle", ready: false, auto_enabled: false, freshness: "stale", stale: true, revision: 0 });
    snapshot.capabilities.read_chat = false;
    await mount();
    expect(step("key").getAttribute("data-status")).toBe("process");
    expect(step("crm").getAttribute("data-status")).toBe("wait");
    expect(syncButton().textContent).toBe("密钥验证中");
    expect(syncButton().disabled).toBe(true);
    expect(container.textContent).toContain("无需重复点击");
    await act(async () => syncButton().click());

    Object.assign(snapshot.source, { key_validation: "valid", auto_enabled: true, phase: "syncing", syncing: true, pending: true, freshness: "syncing", source_revision: 2 });
    await poll();
    expect(step("key").getAttribute("data-status")).toBe("finish");
    expect(step("crm").getAttribute("data-status")).toBe("process");
    expect(syncButton().textContent).toBe("同步中");
    expect(syncButton().disabled).toBe(true);
    expect(accountSession.get().snapshot?.source).toMatchObject({ ready: false, revision: 0 });
    expect(container.textContent).not.toContain("聊天已同步");

    Object.assign(snapshot.source, { phase: "ready", ready: true, syncing: false, pending: false, stale: false, freshness: "fresh", revision: 2, applied_source_revision: 2 });
    snapshot.capabilities.read_chat = true;
    await poll();
    expect(step("crm").getAttribute("data-status")).toBe("finish");
    expect(container.textContent).toContain("聊天已更新");
    expect(syncButton().disabled).toBe(false);
    expect(mocks.backend.getConnection).toHaveBeenCalledTimes(3);
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
    expect(mocks.backend.connectClient).not.toHaveBeenCalled();
  });

  it.each(["key_store_unreadable", "source_unreadable"])("shows automatic retry for %s and resumes verification from observation alone", async (errorCode) => {
    Object.assign(snapshot.source, { key_validation: "unverified", phase: "error", ready: false, auto_enabled: false, last_error: "暂时无法读取", error_code: errorCode, retry_at: Date.now() / 1000 + 5, freshness: "stale", stale: true });
    snapshot.capabilities.read_chat = false;
    await mount();
    expect(container.textContent).toContain("后台将自动重试，无需重复点击");
    expect(container.textContent).toContain("自动重试时间");
    expect(container.textContent).not.toContain("修正后点击");
    expect(syncButton().textContent).toBe("等待自动重试");
    expect(syncButton().disabled).toBe(true);
    expect(step("key").getAttribute("data-status")).toBe("wait");
    await act(async () => syncButton().click());
    await poll();
    expect(syncButton().disabled).toBe(true);
    Object.assign(snapshot.source, { key_validation: "verifying", last_error: null, error_code: null, retry_at: null, phase: "idle" });
    await poll();
    expect(syncButton().textContent).toBe("密钥验证中");
    expect(step("key").getAttribute("data-status")).toBe("process");
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
  });

  it("shows pending CRM backoff as waiting until a retry is actually running", async () => {
    const retryAt = Date.now() / 1000 + 60;
    Object.assign(snapshot.source, { key_validation: "valid", phase: "error", ready: false, pending: true, syncing: false, last_error: "CRM 暂时无法写入", error_code: "sync_error", retry_at: retryAt, freshness: "stale", stale: true });
    snapshot.capabilities.read_chat = false;
    await mount();
    expect(step("crm").getAttribute("data-status")).toBe("wait");
    expect(step("crm").textContent).toContain("等待自动重试");
    expect(syncButton().textContent).toBe("等待自动重试");
    expect(syncButton().disabled).toBe(true);
    expect(container.textContent).toContain(`自动重试时间：${new Date(retryAt * 1000).toLocaleString()}`);
    expect(container.textContent).not.toContain("正在重试同步");
    expect(container.textContent).not.toContain("正在自动验证密钥或同步聊天");
    await act(async () => syncButton().click());
    await poll();
    expect(step("crm").getAttribute("data-status")).toBe("wait");

    snapshot.source.syncing = true;
    await poll();
    expect(step("crm").getAttribute("data-status")).toBe("process");
    expect(step("crm").textContent).toContain("正在同步聊天");
    expect(syncButton().textContent).toBe("同步中");
    expect(syncButton().disabled).toBe(true);
    expect(container.textContent).toContain("正在重试同步");
    expect(container.textContent).not.toContain("自动重试时间");
    expect(mocks.backend.retryConnection).not.toHaveBeenCalled();
  });

  it.each(["unavailable", "invalid"] as const)("requires manual handling for a terminal %s key", async (keyValidation) => {
    Object.assign(snapshot.source, { key_validation: keyValidation, phase: "error", ready: false, auto_enabled: false, last_error: "请更新密钥", retry_at: null, freshness: "stale", stale: true });
    await mount();
    expect(step("key").getAttribute("data-status")).toBe("error");
    expect(container.textContent).toContain("修正后点击");
    expect(container.textContent).not.toContain("后台将自动重试");
    expect(syncButton().disabled).toBe(false);
    await act(async () => syncButton().click());
    expect(mocks.backend.retryConnection).toHaveBeenCalledExactlyOnceWith(snapshot.account.epoch);
  });
});
