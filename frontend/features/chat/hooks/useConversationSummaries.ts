"use client";

import { App } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import type { Conversation } from "@/types/chatCanonical";

export function useConversationSummaries() {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const { snapshot } = useAccount();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [polledRevision, setPolledRevision] = useState(0);
  const polling = useRef(false);
  // The source revision can advance before CRM synchronization finishes.
  const revision = JSON.stringify([Math.max(snapshot?.source.revision ?? 0, polledRevision), snapshot?.source.phase, snapshot?.source.last_success]);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setConversations(await backend.listConversations());
    } catch (error) {
      if (!(error instanceof AccountChangedError)) message.error("聊天数据加载失败，请刷新状态后重试");
    } finally {
      setLoading(false);
    }
  }, [backend, message]);

  useEffect(() => {
    if (!snapshot?.account.self_ali_id || snapshot.source.key_validation !== "valid") return;
    let cancelled = false;
    const poll = async () => {
      if (cancelled || document.hidden || polling.current) return;
      polling.current = true;
      try {
        const next = await backend.getConversationRevision();
        if (!cancelled && next.ready) setPolledRevision(next.revision);
      } catch {
        // A busy backend or expired workspace must not discard the visible archive.
      } finally {
        polling.current = false;
      }
    };
    const timer = setInterval(() => void poll(), 10_000);
    const onVisible = () => { if (!document.hidden) void poll(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [backend, snapshot?.account.self_ali_id, snapshot?.source.key_validation]);

  useEffect(() => {
    // Reload the list when the shared source revision changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload, revision]);
  return { conversations, loading, reload, revision };
}
