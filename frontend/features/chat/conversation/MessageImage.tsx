"use client";

import { Image, Spin } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { useImageBlob } from "../hooks/useImageBlob";
import type { ChatMessage } from "@/types/chatCanonical";

const MAX_WIDTH = 240;
const FALLBACK_SIZE = 160;

export function MessageImage({ conversationId, message, load }: {
  conversationId: string;
  message: ChatMessage;
  load: (conversationId: string, externalMid: string) => Promise<Blob>;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === "undefined");
  const externalMid = message.externalMid ?? message.id;
  const fetchImage = useCallback(() => load(conversationId, externalMid), [load, conversationId, externalMid]);
  const { state, retry } = useImageBlob(fetchImage, visible);

  useEffect(() => {
    const node = holder.current;
    if (visible || !node) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setVisible(true);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  const width = message.image?.width || FALLBACK_SIZE;
  const height = message.image?.height || FALLBACK_SIZE;
  const shownWidth = Math.min(width, MAX_WIDTH);
  const aspectRatio = `${width} / ${height}`;

  return (
    <div ref={holder} className="relative max-w-full overflow-hidden rounded-lg bg-slate-100" style={{ width: shownWidth, aspectRatio }}>
      {state.phase === "ready" ? (
        <Image src={state.url} alt="聊天图片" width="100%" height="100%" style={{ objectFit: "contain" }} />
      ) : state.phase === "failed" ? (
        <div className="flex h-full flex-col items-center justify-center gap-1 p-2 text-xs text-slate-500" role="alert">
          <span>{state.text}</span>
          {state.text === "图片加载失败" ? (
            <button type="button" className="text-blue-500 underline-offset-2 hover:underline" onClick={retry}>重试</button>
          ) : null}
        </div>
      ) : (
        <div className="flex h-full items-center justify-center" role="status" aria-label="图片加载中"><Spin size="small" /></div>
      )}
    </div>
  );
}
