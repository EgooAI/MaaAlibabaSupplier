// @vitest-environment happy-dom
import { act, useEffect, type ReactNode, type ButtonHTMLAttributes, type InputHTMLAttributes } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { HomePage } from "@/features/inbox/HomePage";
import { InboxSettingsCard } from "@/features/settings/InboxSettingsCard";
import { ConversationFilters } from "@/features/chat/conversation/ConversationFilters";
import { inboxQueryFromUrl } from "@/domain/chat/inboxModel";
import { connectionSnapshot } from "@/mock/connectionData";
import { accountSession } from "@/services/accountSession";
import type { InboxOverview } from "@/types/inbox";

const mocks = vi.hoisted(() => ({
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
  backend: { getConnection: vi.fn(), getInboxOverview: vi.fn(), getInboxSettings: vi.fn(), saveInboxSettings: vi.fn(), listConversations: vi.fn() },
}));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("@/features/settings/SettingsPage", () => ({ AccountSetup: () => <p>账号接入引导</p> }));
vi.mock("@/features/account/SyncStatus", () => ({ SyncStatus: () => <p>同步状态</p> }));
vi.mock("next/link", () => ({ default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a> }));
vi.mock("antd", () => {
  const Group = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  const Input = ({ value, placeholder, onChange, "aria-label": label }: InputHTMLAttributes<HTMLInputElement>) => <input value={value} placeholder={placeholder} aria-label={label} onInput={(event) => onChange?.({ ...event, target: event.currentTarget })} />;
  return {
    App: { useApp: () => ({ message: mocks.message }) },
    Space: Object.assign(Group, { Compact: Group }),
    Input: Object.assign(Input, { Search: ({ onSearch, ...props }: InputHTMLAttributes<HTMLInputElement> & { onSearch: (value: string) => void }) => <><Input {...props} /><button onClick={() => onSearch(String(props.value ?? ""))}>搜索</button></> }),
    Popover: ({ open, onOpenChange, content, children }: { open: boolean; onOpenChange: (open: boolean) => void; content: ReactNode; children: ReactNode }) => <><div onClick={() => onOpenChange(!open)}>{children}</div>{open ? content : null}</>,
    Checkbox: ({ children, onChange, ...props }: InputHTMLAttributes<HTMLInputElement>) => <label><input type="checkbox" {...props} onChange={onChange} />{children}</label>,
    Select: ({ value, onChange, options, "aria-label": label }: { value?: string; onChange: (value: string) => void; options: { value: string; label: string }[]; "aria-label": string }) => <select aria-label={label} value={value ?? ""} onChange={(event) => onChange(event.target.value)}><option value="" />{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>,
    Typography: { Text: Group, Title: Group },
    Card: ({ title, children }: { title?: string; children: ReactNode }) => <section>{title}{children}</section>,
    Statistic: ({ title, value }: { title: string; value: ReactNode }) => <p>{title}: <output>{value}</output></p>,
    Alert: ({ title, action }: { title: string; action: ReactNode }) => <aside>{title}{action}</aside>,
    Button: ({ children, disabled, loading, onClick, htmlType, "aria-label": label }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean; htmlType?: "button" | "submit" }) => <button type={htmlType ?? "button"} aria-label={label} disabled={disabled || loading} onClick={onClick}>{children}</button>,
    InputNumber: ({ value, onChange, disabled, min, max, "aria-label": label }: { value: number | null; onChange: (value: number | null) => void; disabled: boolean; min: number; max: number; "aria-label": string }) => <input type="number" aria-label={label} value={value ?? ""} disabled={disabled} min={min} max={max} onInput={(event) => onChange(event.currentTarget.value === "" ? null : Number(event.currentTarget.value))} />,
  };
});

const overview: InboxOverview = { counts: { total: 320, unread: 12, needs_reply: 9, overdue: 4, history_pending: 80, waiting_customer: 200 }, timeout_seconds: 86400, inbox_revision: 1, updated_at: 1789600000 };
let root: Root;
let container: HTMLDivElement;
let account: ReturnType<typeof useAccount>;
function Probe() {
  const current = useAccount();
  useEffect(() => { account = current; }, [current]);
  return null;
}
async function mount(settings = false) {
  await act(async () => root.render(<AccountProvider><Probe />{settings ? <InboxSettingsCard /> : <HomePage />}</AccountProvider>));
}
async function setHours(value: string) {
  await act(async () => {
    const input = container.querySelector("input")!;
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  accountSession.invalidate();
  localStorage.clear();
  mocks.backend.getConnection.mockResolvedValue(structuredClone(connectionSnapshot));
  mocks.backend.getInboxOverview.mockResolvedValue(structuredClone(overview));
  mocks.backend.getInboxSettings.mockResolvedValue({ timeout_seconds: 86400, inbox_revision: 1 });
  mocks.backend.saveInboxSettings.mockImplementation(async (timeout_seconds: number) => ({ timeout_seconds, inbox_revision: 2 }));
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

describe("inbox home and settings", () => {
  it("uses account-wide counts, shows sync status and links to matching filter controls", async () => {
    await mount();
    expect(container.textContent).not.toContain("账号接入引导");
    expect(container.textContent).toContain("同步状态");
    expect([...container.querySelectorAll("output")].map((node) => node.textContent)).toEqual(["9", "4", "12", "80", "200"]);
    expect(container.textContent).toContain("全部会话 320 个");
    expect(mocks.backend.listConversations).not.toHaveBeenCalled();
    const links = [...container.querySelectorAll("a")].slice(0, 5).map((anchor) => anchor.getAttribute("href")!);
    expect(links).toEqual([
      "/chat/customer-sessions?reply_state=needs_reply", "/chat/customer-sessions?reply_state=needs_reply&overdue=true", "/chat/customer-sessions?unread=true", "/chat/customer-sessions?reply_state=history_pending", "/chat/customer-sessions?reply_state=waiting_customer",
    ]);
    for (const href of links) {
      const query = inboxQueryFromUrl(new URL(href, "http://localhost").searchParams);
      const apply = vi.fn();
      await act(async () => root.render(<ConversationFilters key={href} query={query} onChange={apply} />));
      expect(container.querySelector("form")).toBeNull();
      await act(async () => container.querySelector<HTMLButtonElement>('[aria-label^="筛选会话"]')!.click());
      expect(container.querySelector<HTMLSelectElement>('[aria-label="回复状态"]')!.value).toBe(query.reply_state ?? "");
      expect(container.querySelectorAll<HTMLInputElement>('[type="checkbox"]')[0].checked).toBe(query.unread === true);
      expect(container.querySelectorAll<HTMLInputElement>('[type="checkbox"]')[1].checked).toBe(query.overdue === true);
      await act(async () => container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
      expect(apply).toHaveBeenCalledExactlyOnceWith(query);
    }
  });

  it("keeps setup for accounts without a readable archive", async () => {
    mocks.backend.getConnection.mockResolvedValue({ ...connectionSnapshot, capabilities: { ...connectionSnapshot.capabilities, read_chat: false } });
    await mount();
    expect(container.textContent).toContain("账号接入引导");
    expect(mocks.backend.getInboxOverview).not.toHaveBeenCalled();
  });

  it("preserves visible statistics on failure or suspension and refreshes deadlines periodically", async () => {
    await mount();
    const card = container.querySelector("section");
    mocks.backend.getInboxOverview.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(container.textContent).toContain("统计读取失败");
    expect(container.querySelector("section")).toBe(card);
    expect(container.querySelectorAll("output")[1].textContent).toBe("4");
    mocks.backend.getConnection.mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await account.refresh(); });
    expect(container.querySelector("section")).toBe(card);
    mocks.backend.getInboxOverview.mockResolvedValue({ ...overview, counts: { ...overview.counts, overdue: 5 } });
    await act(async () => { await account.refresh(); });
    expect(container.querySelectorAll("output")[1].textContent).toBe("5");
    expect(container.textContent).not.toContain("统计读取失败");
  });

  it("discards the previous account's overview after switching accounts", async () => {
    let finish!: (value: InboxOverview) => void;
    mocks.backend.getInboxOverview.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await mount();
    mocks.backend.getConnection.mockResolvedValue({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "b", self_ali_id: "b" } });
    mocks.backend.getInboxOverview.mockResolvedValue({ ...overview, counts: { ...overview.counts, needs_reply: 2 } });
    await act(async () => { await account.refresh(); });
    await act(async () => finish(overview));
    expect(container.querySelector("output")?.textContent).toBe("2");
  });

  it("submits search explicitly, preserving literal percent and underscore text", async () => {
    const apply = vi.fn();
    await act(async () => root.render(<ConversationFilters query={{ search_scope: "messages" }} onChange={apply} />));
    await act(async () => {
      const input = container.querySelector<HTMLInputElement>('[aria-label="搜索内容"]')!;
      input.value = "quote%_ &客户";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(apply).not.toHaveBeenCalled();
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "搜索")!.click());
    expect(apply).toHaveBeenCalledExactlyOnceWith({ search_scope: "messages", q: "quote%_ &客户" });
  });

  it("discards unapplied filters when reopening and clears applied filters on reset", async () => {
    const apply = vi.fn();
    await act(async () => root.render(<ConversationFilters query={{ country: "ES", unread: true }} onChange={apply} />));
    const toggle = async () => { await act(async () => container.querySelector<HTMLButtonElement>('[aria-label^="筛选会话"]')!.click()); };
    await toggle();
    await act(async () => {
      const input = container.querySelector<HTMLInputElement>('[aria-label="国家"]')!;
      input.value = "US";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await toggle();
    expect(apply).not.toHaveBeenCalled();
    await toggle();
    expect(container.querySelector<HTMLInputElement>('[aria-label="国家"]')!.value).toBe("ES");
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "重置")!.click());
    expect(apply).toHaveBeenCalledExactlyOnceWith({ search_scope: "all" });
    expect(container.querySelector("form")).toBeNull();
    expect(container.querySelector('[aria-label="筛选会话"]')).not.toBeNull();
  });

  it("reads and saves 1..168 hours without invalidating the epoch, remounting, or changing drafts", async () => {
    await mount(true);
    const epoch = account.snapshot?.account.epoch;
    const generation = account.generation;
    const card = container.querySelector("section");
    localStorage.setItem("draft", "unsent");
    expect(container.querySelector("input")!.value).toBe("24");
    expect(account.readRefreshSequence).toBe(0);
    for (const value of ["", "0", "169"]) {
      await setHours(value);
      expect(container.querySelector("button")!.disabled).toBe(true);
    }
    for (const value of ["1", "168"]) {
      await setHours(value);
      await act(async () => container.querySelector("button")!.click());
      expect(mocks.backend.saveInboxSettings).toHaveBeenLastCalledWith(Number(value) * 3600);
    }
    expect(account.snapshot?.account.epoch).toBe(epoch);
    expect(account.generation).toBe(generation);
    expect(account.readRefreshSequence).toBe(2);
    expect(container.querySelector("section")).toBe(card);
    expect(localStorage.getItem("draft")).toBe("unsent");
    expect(localStorage.getItem("maa:account-changed")).toBeNull();
  });

  it("keeps failed settings edits and rejects a save completed after account switching", async () => {
    await mount(true);
    await setHours("48");
    mocks.backend.saveInboxSettings.mockRejectedValueOnce(new Error("offline"));
    await act(async () => container.querySelector("button")!.click());
    expect(container.querySelector("input")!.value).toBe("48");
    expect(account.readRefreshSequence).toBe(0);
    let finish!: (value: { timeout_seconds: number; inbox_revision: number }) => void;
    mocks.backend.saveInboxSettings.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await act(async () => container.querySelector("button")!.click());
    mocks.backend.getConnection.mockResolvedValue({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "b", data_dir: "E:\\other" } });
    mocks.backend.getInboxSettings.mockResolvedValue({ timeout_seconds: 7200, inbox_revision: 1 });
    await act(async () => { await account.refresh(); });
    await act(async () => finish({ timeout_seconds: 172800, inbox_revision: 8 }));
    expect(container.querySelector("input")!.value).toBe("2");
    expect(account.readRefreshSequence).toBe(0);
  });
});
