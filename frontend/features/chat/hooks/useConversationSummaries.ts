"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import type { Conversation } from "@/types/chatCanonical";

type ReadState = { pending: boolean; success: boolean; failureVersion: number };

export function useConversationSummaries() {
  const backend = useAccountBackend();
  const { snapshot, blocked, readRefreshSequence } = useAccount();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [polledRevision, setPolledRevision] = useState(0);
  const [recovery, setRecovery] = useState(0);
  const [refreshError, setRefreshError] = useState(false);
  const [refreshPending, setRefreshPending] = useState(false);
  const needsRecovery = useRef(false);
  const recoveryQueued = useRef(false);
  const failureVersion = useRef(0);
  const reads = useRef<Partial<Record<"list" | "detail", ReadState>>>({});
  const listRequest = useRef(0);
  const polling = useRef(false);
  const revision = `${Math.max(snapshot?.source.revision ?? 0, polledRevision)}:${readRefreshSequence}:${recovery}`;
  const markReloadNeeded = useCallback(() => {
    ++failureVersion.current;
    needsRecovery.current = true;
    setRefreshError(true);
  }, []);
  const beginRead = useCallback((kind: "list" | "detail") => {
    const state: ReadState = { pending: true, success: false, failureVersion: failureVersion.current };
    reads.current[kind] = state;
    setRefreshPending(true);
    return (success: boolean) => {
      if (reads.current[kind] !== state) return;
      state.pending = false;
      state.success = success;
      if (!success) markReloadNeeded();
      const required = Object.values(reads.current);
      const pending = required.some((read) => read.pending);
      setRefreshPending(pending);
      // A revision response is not an acknowledgement of the actual data reads.
      if (!pending && required.every((read) => read.success && read.failureVersion === failureVersion.current)) {
        needsRecovery.current = false;
        setRefreshError(false);
      }
    };
  }, [markReloadNeeded]);
  const reload = useCallback(async () => {
    recoveryQueued.current = false;
    const request = ++listRequest.current;
    const acknowledge = beginRead("list");
    try {
      const next = await backend.listConversations();
      if (request === listRequest.current) {
        setConversations(next);
        acknowledge(true);
      }
    } catch {
      acknowledge(false);
    } finally {
      if (request === listRequest.current) setLoading(false);
    }
  }, [backend, beginRead]);

  useEffect(() => () => { ++listRequest.current; reads.current = {}; }, []);

  useEffect(() => {
    if (blocked) {
      ++failureVersion.current;
      needsRecovery.current = true;
      return;
    }
    if (!snapshot?.account.self_ali_id) return;
    let cancelled = false;
    const poll = async () => {
      if (cancelled || document.hidden || polling.current) return;
      polling.current = true;
      try {
        const next = await backend.getConversationRevision();
        if (!cancelled && next.ready) {
          setPolledRevision(next.revision);
          if (needsRecovery.current && !recoveryQueued.current && !Object.values(reads.current).some((read) => read.pending)) {
            recoveryQueued.current = true;
            setRecovery((value) => value + 1);
          }
        }
      } catch {
        if (!cancelled) markReloadNeeded();
      } finally {
        polling.current = false;
      }
    };
    if (needsRecovery.current) void poll();
    const timer = setInterval(() => void poll(), 10_000);
    const onVisible = () => { if (!document.hidden) void poll(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [backend, blocked, snapshot?.account.self_ali_id, markReloadNeeded]);

  useEffect(() => {
    // Commits, explicit refreshes and recovery each trigger data reads.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload, revision]);
  return { conversations, loading, reload, revision, refreshError, refreshPending, beginRead };
}
