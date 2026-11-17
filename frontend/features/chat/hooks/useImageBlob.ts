"use client";

import { useEffect, useState } from "react";
import { AccountChangedError } from "@/services/accountSession";
import { ApiError } from "@/services/httpAdapter";

type ImageState = { phase: "loading" } | { phase: "ready"; url: string } | { phase: "failed"; text: string };

/** Loads scoped image bytes and releases object URLs when the request identity changes. */
export function useImageBlob(load: () => Promise<Blob>, enabled = true) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ load: typeof load; attempt: number; state: ImageState }>();
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let url: string | undefined;
    load().then((blob) => {
      if (!active) return;
      url = URL.createObjectURL(blob);
      setResult({ load, attempt, state: { phase: "ready", url } });
    }).catch((error) => {
      if (active && !(error instanceof AccountChangedError)) setResult({ load, attempt, state: {
        phase: "failed", text: error instanceof ApiError && error.status === 404 ? "图片已失效" : "图片加载失败",
      } });
    });
    return () => { active = false; if (url) URL.revokeObjectURL(url); };
  }, [load, enabled, attempt]);
  const state: ImageState = enabled && result?.load === load && result.attempt === attempt ? result.state : { phase: "loading" };
  return { state, retry: () => setAttempt((value) => value + 1) };
}
