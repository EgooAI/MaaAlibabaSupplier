"use client";

import { App } from "antd";
import { useCallback, useEffect, useState } from "react";
import { backend } from "@/services/client";
import type { Conversation } from "@/types/chatCanonical";

export function useConversationSummaries() {
  const { message } = App.useApp();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setConversations(await backend.listConversations());
    } catch {
      message.error("聊天数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    async function loadInitialSummaries() {
      await reload();
    }

    void loadInitialSummaries();
  }, [reload]);

  return { conversations, loading, reload };
}
