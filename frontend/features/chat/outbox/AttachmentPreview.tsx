"use client";

import { Image } from "antd";
import { useCallback } from "react";
import { useAccountBackend } from "@/features/account/AccountProvider";
import { useImageBlob } from "../hooks/useImageBlob";

export function AttachmentPreview({ sid, id }: { sid: string; id: string }) {
  const backend = useAccountBackend();
  const load = useCallback(() => backend.getImageAttachmentPreview(sid, id), [backend, sid, id]);
  const { state, retry } = useImageBlob(load);
  return state.phase === "ready" ? <Image src={state.url} alt="发送任务图片" width={200} style={{ maxHeight: 240, objectFit: "contain" }} />
    : state.phase === "failed" ? <button onClick={retry}>{state.text}，点击重试</button> : <span>图片加载中…</span>;
}
