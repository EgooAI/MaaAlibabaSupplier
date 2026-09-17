"use client";

import { App } from "antd";
import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { backend } from "@/services/client";
import { accountSession, AccountChangedError, captureAccount, scopeBackend } from "@/services/accountSession";
import type { AccountEpoch } from "@/types/connection";
import { flushDrafts } from "./draftStorage";

const STORAGE_EVENT_KEY = "maa:account-changed";

function useAccountController() {
  const session = useSyncExternalStore(accountSession.subscribe, accountSession.get, accountSession.server);
  const { message } = App.useApp();
  const [error, setError] = useState<string>();
  const [refreshing, setRefreshing] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [syncBusy, setSyncBusy] = useState(false);
  const [readRefreshSequence, setReadRefreshSequence] = useState(0);
  const syncRequest = useRef(false);
  const request = useRef(0);
  const mutation = useRef(false);
  const mounted = useRef(true);
  const refreshReads = useCallback(() => setReadRefreshSequence((value) => value + 1), []);

  const notify = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_EVENT_KEY, crypto.randomUUID());
    } catch {
      message.warning("无法通知其他标签页，请手动刷新其他已打开的页面");
    }
  }, [message]);

  const refresh = useCallback(async () => {
    if (mutation.current) return;
    const id = ++request.current;
    setRefreshing(true);
    try {
      const snapshot = await backend.getConnection();
      if (!mounted.current || id !== request.current) return;
      flushDrafts(() => localStorage);
      accountSession.accept(snapshot);
      setError(undefined);
      return snapshot;
    } catch (err) {
      if (!mounted.current || id !== request.current) return;
      accountSession.suspend();
      setError(err instanceof Error ? err.message : "接入状态加载失败");
    } finally {
      if (mounted.current && id === request.current) setRefreshing(false);
    }
  }, []);

  const mutate = useCallback(async <T,>(operation: (epoch: AccountEpoch) => Promise<T>): Promise<T> => {
    if (mutation.current) throw new Error("账号设置正在更新，请稍候");
    const { epoch } = captureAccount();
    mutation.current = true;
    ++request.current;
    accountSession.invalidate();
    setMutating(true);
    notify();
    try {
      return await operation(epoch);
    } finally {
      mutation.current = false;
      // A failed write may have reached the server; always reconcile authoritatively.
      await refresh();
      setMutating(false);
      notify();
    }
  }, [notify, refresh]);

  const requestSync = useCallback(async () => {
    if (syncRequest.current) throw new Error("同步请求正在提交，请稍候");
    const ticket = captureAccount();
    syncRequest.current = true;
    setSyncBusy(true);
    try {
      const submitted = await backend.retryConnection(ticket.epoch);
      if (!mounted.current) throw new AccountChangedError();
      ticket.assertCurrent(submitted.account.epoch);
      // Profile writes can change CRM without advancing the IM commit revision.
      setReadRefreshSequence((value) => value + 1);
      // The POST only submits work. Polling observes its eventual commit.
    } catch (error) {
      if (!mounted.current) throw new AccountChangedError();
      ticket.assertCurrent();
      throw error;
    } finally {
      syncRequest.current = false;
      if (mounted.current) setSyncBusy(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    // Initial observation also exposes its loading state to the connection card.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
    const visibleRefresh = () => { if (!document.hidden) void refresh(); };
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_EVENT_KEY && event.key !== null) return;
      ++request.current;
      accountSession.invalidate();
      void refresh();
    };
    window.addEventListener("focus", visibleRefresh);
    window.addEventListener("storage", onStorage);
    document.addEventListener("visibilitychange", visibleRefresh);
    const timer = setInterval(visibleRefresh, 10_000);
    return () => {
      mounted.current = false;
      // Invalidate the latest request, rather than the request present at mount.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      ++request.current;
      clearInterval(timer);
      window.removeEventListener("focus", visibleRefresh);
      window.removeEventListener("storage", onStorage);
      document.removeEventListener("visibilitychange", visibleRefresh);
    };
  }, [refresh]);

  return { ...session, error, refreshing, mutating, syncBusy, readRefreshSequence, refreshReads, refresh, mutate, requestSync };
}

const AccountContext = createContext<ReturnType<typeof useAccountController> | null>(null);

export function AccountProvider({ children }: { children: ReactNode }) {
  const value = useAccountController();
  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>;
}

export function useAccount() {
  const value = useContext(AccountContext);
  if (!value) throw new Error("AccountProvider is required");
  return value;
}

export function useAccountBackend() {
  const active = useRef(true);
  // The predicate reads the ref only when a request starts or settles, never during render.
  // eslint-disable-next-line react-hooks/refs
  const [scoped] = useState(() => scopeBackend(backend, () => active.current));
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  return scoped;
}

export { AccountChangedError };
