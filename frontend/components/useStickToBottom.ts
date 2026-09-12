"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isNearBottom } from "@/domain/chat/scrollModel";

type StickToBottomInput = {
  /** Switches the timeline identity (conversation/session id): always jumps to bottom. */
  sessionKey: string;
  /** Grows when content is appended (message count): follows only when pinned. */
  followKey: number;
};

export function useStickToBottom({ sessionKey, followKey }: StickToBottomInput) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);
  const prevSessionKeyRef = useRef(sessionKey);
  const [showJumpButton, setShowJumpButton] = useState(false);

  const updatePinned = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    pinnedRef.current = isNearBottom(element);
    setShowJumpButton(!pinnedRef.current && element.scrollHeight > element.clientHeight);
  }, []);

  const scrollToBottom = useCallback((smooth = false) => {
    const element = scrollRef.current;
    if (!element) return;
    pinnedRef.current = true;
    setShowJumpButton(false);
    if (smooth) element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    else element.scrollTop = element.scrollHeight;
  }, []);

  useEffect(() => {
    const sessionChanged = prevSessionKeyRef.current !== sessionKey;
    prevSessionKeyRef.current = sessionKey;
    if (sessionChanged || pinnedRef.current) scrollToBottom();
  }, [sessionKey, followKey, scrollToBottom]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (pinnedRef.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return { scrollRef, showJumpButton, handleScroll: updatePinned, scrollToBottom };
}
