"use client";

import { App } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "@/services/client";
import type { Conversation } from "@/types/chatCanonical";

// Revision probe cadence while the chat page is visible. Each probe is
// fingerprint-gated on the backend (microseconds when unchanged); the full
// list reloads only when the revision actually moved.
const REVISION_POLL_INTERVAL_MS = 10_000;

export function useConversationSummaries() {
  const { message } = App.useApp();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const revisionRef = useRef(0);
  const baselineReadyRef = useRef(false);

  const noteRevision = useCallback((next: number) => {
    baselineReadyRef.current = true;
    if (revisionRef.current === next) return;
    revisionRef.current = next;
    setRevision(next);
  }, []);

  const probeRevision = useCallback(async (): Promise<number | null> => {
    try {
      const probe = await backend.getConversationRevision();
      return probe.ready ? probe.revision : null;
    } catch {
      return null;
    }
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      // Two passes max: if the source moves mid-fetch, the second pass
      // converges instead of leaving a fresh baseline over stale list data.
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const before = await probeRevision();
        setConversations(await backend.listConversations());
        const after = await probeRevision();
        if (after !== null) noteRevision(after);
        if (after === null || after === before) break;
      }
    } catch {
      message.error("聊天数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [message, noteRevision, probeRevision]);

  useEffect(() => {
    async function loadInitialSummaries() {
      await reload();
    }

    void loadInitialSummaries();
  }, [reload]);

  useEffect(() => {
    async function tick() {
      if (document.hidden) return;
      let probe;
      try {
        probe = await backend.getConversationRevision();
      } catch {
        return;
      }
      if (!probe.ready) return;
      if (!baselineReadyRef.current) {
        // Baseline not established yet (initial load still in flight).
        noteRevision(probe.revision);
        return;
      }
      if (probe.revision === revisionRef.current) return;
      noteRevision(probe.revision);
      await reload();
    }

    const timer = setInterval(() => {
      void tick();
    }, REVISION_POLL_INTERVAL_MS);
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [noteRevision, reload]);

  return { conversations, loading, reload, revision };
}
