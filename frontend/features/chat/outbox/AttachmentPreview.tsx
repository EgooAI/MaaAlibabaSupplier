"use client";

import { Image } from "antd";
import { useEffect, useState } from "react";
import { useAccountBackend } from "@/features/account/AccountProvider";
import { AccountChangedError } from "@/services/accountSession";

export function AttachmentPreview({ sid, id }: { sid: string; id: string }) {
  const backend = useAccountBackend();
  const [url, setUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    let objectUrl: string | undefined;
    backend.getImageAttachmentPreview(sid, id).then((blob) => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); setFailed(false);
    }).catch((error) => { if (active && !(error instanceof AccountChangedError)) setFailed(true); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [backend, sid, id, attempt]);
  return url ? <Image src={url} alt="发送任务图片" width={200} style={{ maxHeight: 240, objectFit: "contain" }} />
    : failed ? <button onClick={() => setAttempt((value) => value + 1)}>图片加载失败，点击重试</button> : <span>图片加载中…</span>;
}
