"use client";

import { useCallback, useEffect, useState } from "react";
import { useAccountBackend } from "@/features/account/AccountProvider";
import type { PortraitTag } from "@/types/portrait";

export function usePortraitTags() {
  const backend = useAccountBackend();
  const [tags, setTags] = useState<PortraitTag[]>([]);
  const load = useCallback(async () => {
    try {
      const result = await backend.listPortraitTags();
      setTags(result.tags);
    } catch {
      // Tag definitions are advisory for filters and editors; keep the last list.
    }
  }, [backend]);
  const add = useCallback((tag: PortraitTag) => {
    setTags((current) => current.some((item) => item.tag_id === tag.tag_id) ? current : [...current, tag]);
  }, []);
  useEffect(() => {
    // Tag definitions are external data; the setState happens after the request settles.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  return { tags, reload: load, add };
}
