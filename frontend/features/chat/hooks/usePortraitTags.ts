"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAccountBackend } from "@/features/account/AccountProvider";
import type { PortraitTag } from "@/types/portrait";

export function usePortraitTags(tagRevision?: number) {
  const backend = useAccountBackend();
  const [tags, setTags] = useState<PortraitTag[]>([]);
  const loadedRevision = useRef<number | undefined>(undefined);
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
  useEffect(() => {
    // 标签定义由 CRM 库内各工作区共享：提高共享版本说明其他工作区改动了标签目录。
    // 首个观测值只作为基线；之后严格递增才重读。
    if (typeof tagRevision !== "number") return;
    const previous = loadedRevision.current;
    loadedRevision.current = tagRevision;
    if (previous !== undefined && tagRevision > previous) void load();
  }, [load, tagRevision]);
  return { tags, reload: load, add };
}
