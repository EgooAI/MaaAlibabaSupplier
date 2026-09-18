// @vitest-environment happy-dom
import { act, useEffect, type ReactNode, type ButtonHTMLAttributes, type InputHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { AntdProviders } from "@/components/AntdProviders";
import { useAccount } from "@/features/account/AccountProvider";
import { useAuth } from "@/features/auth/AuthProvider";
import { authSession, AUTH_STORAGE_KEY } from "@/services/authSession";
import { httpBackend } from "@/services/httpAdapter";
import { connectionSnapshot } from "@/mock/connectionData";
import { memoryLocks, memoryStorage } from "@/test/support/authFixture";

vi.mock("antd", () => {
  const Group = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  return {
    App: Object.assign(Group, { useApp: () => ({ message: { warning: vi.fn() } }) }),
    ConfigProvider: Group, theme: {}, Card: Group, Space: Group,
    Typography: { Title: Group, Text: Group },
    Alert: ({ title }: { title: string }) => <aside role="alert">{title}</aside>,
    Spin: () => <span />,
    Input: { Password: ({ onChange, ...props }: InputHTMLAttributes<HTMLInputElement>) => <input type="password" {...props} onInput={(event) => onChange?.({ ...event, target: event.currentTarget })} /> },
    Button: ({ children, disabled, onClick, htmlType }: ButtonHTMLAttributes<HTMLButtonElement> & { htmlType?: "button" | "submit" }) => <button type={htmlType ?? "button"} disabled={disabled} onClick={onClick}>{children}</button>,
  };
});

const response = (data: unknown, status = 200, headers?: HeadersInit) => new Response(JSON.stringify({ code: status === 200 ? 0 : 1, msg: "ok", data }), { status, headers });
let container: HTMLDivElement;
let root: Root;
let mounts: number;
function Workspace() {
  const { snapshot } = useAccount();
  const { logout } = useAuth();
  useEffect(() => { mounts++; }, []);
  return <section data-workspace>{snapshot?.account.self_ali_id}<button onClick={() => void logout()}>退出工作区</button></section>;
}
async function mount() {
  await act(async () => root.render(<AntdProviders><Workspace /></AntdProviders>));
}
async function click(label: string) {
  const button = [...container.querySelectorAll("button")].find((item) => item.textContent === label)!;
  expect(button).toBeTruthy();
  await act(async () => button.click());
}

beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(Math.max(Date.now(), authSession.get().retryAt + 1));
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("localStorage", memoryStorage());
  vi.stubGlobal("navigator", { locks: memoryLocks() });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  localStorage.clear();
  await authSession.bootstrap();
  window.history.replaceState(null, "", "/chat/customer-sessions/?conversation=42#draft");
  mounts = 0;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("global authentication gate", () => {
  it("keeps all account polling unmounted until verification, preserves deep links, and stops polling on cross-tab clear", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, "saved");
    let finish!: (value: Response) => void;
    vi.mocked(fetch).mockImplementation((url) => url === "/api/auth/session"
      ? new Promise<Response>((resolve) => { finish = resolve; })
      : Promise.resolve(response(connectionSnapshot)));
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(mounts).toBe(0);
    expect(container.querySelector("[data-workspace]")).toBeNull();
    await act(async () => { finish(response({ authenticated: true })); });
    expect(mounts).toBe(1);
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual(["/api/auth/session", "/api/settings/connection"]);
    expect(window.location.pathname + window.location.search + window.location.hash).toBe("/chat/customer-sessions/?conversation=42#draft");
    await act(async () => {
      localStorage.clear();
      window.dispatchEvent(new StorageEvent("storage", { key: null }));
      await vi.advanceTimersByTimeAsync(30_000);
      window.dispatchEvent(new Event("focus"));
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(container.querySelector("[data-workspace]")).toBeNull();
    expect(container.querySelector("input[type=password]")).not.toBeNull();
  });

  it("shows a Retry-After countdown, retains the saved token, and verifies only on retry", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, "saved");
    vi.mocked(fetch).mockResolvedValueOnce(response(null, 429, { "Retry-After": "3" }));
    await mount();
    expect(container.textContent).toContain("3 秒");
    const retry = [...container.querySelectorAll("button")].find((item) => item.textContent === "重新验证登录")!;
    expect(retry.disabled).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(retry.disabled).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(AUTH_STORAGE_KEY)).toBe("saved");
    vi.mocked(fetch).mockResolvedValueOnce(response({ authenticated: true })).mockResolvedValue(response(connectionSnapshot));
    await click("重新验证登录");
    expect(container.querySelector("[data-workspace]")).not.toBeNull();
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("requires an explicit temporary-session choice, clears the password, and shows logout failure", async () => {
    await mount();
    vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new Error("quota"); });
    vi.mocked(fetch).mockImplementation((url) => {
      if (url === "/api/auth/login") return Promise.resolve(response({ token: "temporary" }));
      if (url === "/api/auth/logout") return Promise.reject(new Error("offline"));
      return Promise.resolve(response(connectionSnapshot));
    });
    await act(async () => {
      const input = container.querySelector("input")!;
      input.value = "secret";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    // WebCrypto completes on the native event loop rather than fake timers.
    await act(async () => { await vi.waitFor(() => expect(authSession.get().phase).toBe("persist")); });
    expect(container.querySelector("input")).toBeNull();
    expect(mounts).toBe(0);
    expect(container.textContent).toContain("无法保存登录凭证");
    await click("仅在本页临时登录");
    expect(mounts).toBe(1);
    await click("退出工作区");
    expect(container.querySelector("[data-workspace]")).toBeNull();
    expect(container.textContent).toContain("撤销凭证失败");
    const count = vi.mocked(fetch).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetch).toHaveBeenCalledTimes(count);
  });

  it("unmounts the workspace on a current 401 without replaying its mutation", async () => {
    localStorage.setItem(AUTH_STORAGE_KEY, "saved");
    vi.mocked(fetch).mockResolvedValueOnce(response({ authenticated: true })).mockResolvedValueOnce(response(connectionSnapshot));
    await mount();
    vi.mocked(fetch).mockResolvedValue(new Response("", { status: 401 }));
    await act(async () => { await expect(httpBackend.resetCache()).rejects.toThrow("登录状态已变化"); });
    expect(container.querySelector("[data-workspace]")).toBeNull();
    expect(container.textContent).toContain("登录凭证已失效");
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
