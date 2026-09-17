"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import type { Conversation } from "@/types/chatCanonical";
import type { ConversationPage, ConversationQuery } from "@/types/inbox";
import { ApiError } from "@/services/httpAdapter";

type ReadState = { pending: boolean; success: boolean; failureVersion: number };

export function useConversationSummaries(initialQuery: ConversationQuery = {}) {
  const backend = useAccountBackend();
  const { snapshot, blocked, readRefreshSequence } = useAccount();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [query, setQuery] = useState<ConversationQuery>({ ...initialQuery, offset: 0, limit: 50 });
  const [page, setPage] = useState<ConversationPage>();
  const [pagePending, setPagePending] = useState(true);
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [listVersion, setListVersion] = useState(0);
  const [detailVersion, setDetailVersion] = useState(0);
  const seenInbox = useRef<number | undefined>(undefined);
  const nextDue = useRef<number | null>(null);
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
  const sharedRevision = `${Math.max(snapshot?.source.revision ?? 0, polledRevision)}:${readRefreshSequence}:${recovery}`;
  const listRevision = `${sharedRevision}:${listVersion}`;
  const detailRevision = `${sharedRevision}:${detailVersion}`;
  const deadlineFiltered = query.overdue !== undefined;
  const observeInbox = useCallback((inboxRevision: number, refreshDeadlineFilter = false) => {
    const changed = seenInbox.current === undefined || inboxRevision > seenInbox.current;
    const crossedDue = nextDue.current !== null && nextDue.current <= Date.now() / 1000;
    if (changed) seenInbox.current = inboxRevision;
    if (crossedDue) nextDue.current = null;
    // Observation schedules detail reads; only beginRead acknowledges their success.
    // A list response must not retrigger the list that just observed this version.
    const refresh = changed || crossedDue || refreshDeadlineFilter;
    if (refresh) setDetailVersion((value) => value + 1);
    return refresh;
  }, []);
  const changeQuery = useCallback((next: ConversationQuery) => {
    ++listRequest.current;
    setConversations([]);
    setPage(undefined);
    setPagePending(true);
    setSelectionVersion((value) => value + 1);
    setQuery({ ...next, offset: 0, limit: 50, pagination_revision: undefined });
  }, []);
  const changePage = (offset: number) => {
    if (!page || pagePending) return;
    ++listRequest.current;
    setConversations([]);
    setPage(undefined);
    setPagePending(true);
    setSelectionVersion((value) => value + 1);
    setQuery({ ...query, offset, pagination_revision: offset > 0 ? page.pagination_revision : undefined });
  };
  const markReloadNeeded = useCallback(() => {
    ++failureVersion.current;
    needsRecovery.current = true;
    setRefreshError(true);
  }, []);
  const beginRead = useCallback((kind: "list" | "detail") => {
    const state: ReadState = { pending: true, success: false, failureVersion: failureVersion.current };
    reads.current[kind] = state;
    setRefreshPending(true);
    return (success: boolean, detailDueAt?: number | null) => {
      if (reads.current[kind] !== state) return;
      state.pending = false;
      state.success = success;
      // The retained detail may have a deadline outside the current list page.
      if (success && kind === "detail" && detailDueAt != null) {
        nextDue.current = Math.min(nextDue.current ?? Infinity, detailDueAt);
      }
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
      const next = await backend.listConversations(query);
      if (request === listRequest.current) {
        setConversations(next.items);
        setPage(next);
        observeInbox(next.inbox_revision);
        const due = next.items.flatMap((item) => item.dueAt !== null && !item.isOverdue ? [item.dueAt] : []);
        if (due.length) nextDue.current = Math.min(nextDue.current ?? Infinity, ...due);
        acknowledge(true);
      }
    } catch (error) {
      if (request !== listRequest.current) return;
      if (error instanceof ApiError && error.status === 409 && (query.offset ?? 0) > 0) {
        changeQuery(query);
        return;
      }
      acknowledge(false);
    } finally {
      if (request === listRequest.current) { setLoading(false); setPagePending(false); }
    }
  }, [backend, beginRead, query, changeQuery, observeInbox]);

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
          const reading = Object.values(reads.current).some((read) => read.pending);
          // An empty overdue page cannot supply its own upcoming deadline.
          if (observeInbox(next.inbox_revision, deadlineFiltered && !reading)) {
            setListVersion((value) => value + 1);
          }
          if (next.inbox_revision === seenInbox.current) {
            nextDue.current = next.next_due_at !== null && next.next_due_at > Date.now() / 1000 ? next.next_due_at : null;
          }
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
  }, [backend, blocked, snapshot?.account.self_ali_id, markReloadNeeded, deadlineFiltered, observeInbox]);

  useEffect(() => {
    // Commits, explicit refreshes and recovery each trigger data reads.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload, listRevision]);
  return { conversations, loading, reload, detailRevision, refreshError, refreshPending, beginRead, query, changeQuery, page, pagePending, changePage, selectionVersion };
}
