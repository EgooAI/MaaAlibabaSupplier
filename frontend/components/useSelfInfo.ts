"use client";

import { useCallback, useEffect, useState } from "react";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import type { SelfInfo } from "@/types/home";

export function useSelfInfo() {
  const backend = useAccountBackend();
  const [selfInfo, setSelfInfo] = useState<SelfInfo | null>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [avatarSource, setAvatarSource] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const info = await backend.getSelfInfo();
      setSelfInfo(info);
      setAvatarSource(info?.avatar_url || "");
    } catch (err: unknown) {
      if (err instanceof AccountChangedError) return;
      setSelfInfo(null);
      setAvatarSource("");
      setError(err instanceof Error ? err.message : "个人信息加载失败");
    } finally {
      setLoading(false);
    }
  }, [backend]);

  useEffect(() => {
    // Initial data fetch on mount; setter calls are inside async load, not sync render cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  return { selfInfo, loading, error, avatarSource, setAvatarSource, reload: load };
}
