"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { backend } from "@/services/client";
import { ApiError } from "@/services/httpAdapter";
import { parseInstallAcceptance, parseUpdateState } from "@/services/updateProtocol";
import type { UpdateState } from "@/types/update";

type Action = "status" | "check" | "download" | "install";

export function useAppUpdate() {
  const [state, setState] = useState<UpdateState | null>(null);
  const [pending, setPending] = useState<Action | null>("status");
  const [error, setError] = useState<string | null>(null);
  const [installResult, setInstallResult] = useState<"accepted" | "uncertain" | null>(null);
  const lifetime = useRef({ active: false, request: 0, busy: false });
  const activePhase = state?.phase === "checking" || state?.phase === "downloading";
  const restarting = installResult !== null || state?.phase === "installing";

  async function run(action: Action, candidateId?: string) {
    const scope = lifetime.current;
    if (!scope.active || scope.busy || restarting) return;
    if (action !== "status" && (!state?.supported || activePhase)) return;
    if (action === "download" && (!candidateId || candidateId !== state?.candidate?.id || !["available", "error"].includes(state.phase))) return;
    if (action === "install" && (!candidateId || candidateId !== state?.candidate?.id || state.phase !== "ready" || error)) return;
    scope.busy = true;
    const request = ++scope.request;
    const current = () => scope.active && request === scope.request;
    setPending(action);
    setError(null);
    try {
      if (action === "install") {
        const receipt = await backend.installAppUpdate(candidateId!);
        if (!current()) return;
        parseInstallAcceptance(receipt);
        setInstallResult("accepted");
      } else {
        const value = action === "status" ? await backend.getAppUpdate()
          : action === "check" ? await backend.checkAppUpdate()
            : await backend.downloadAppUpdate(candidateId!);
        if (current()) setState(parseUpdateState(value));
      }
    } catch (cause) {
      if (!current()) return;
      const installRejected = cause instanceof ApiError && (cause.status === 409 || cause.status === 503);
      if (action === "install" && !installRejected) {
        // A lost response may follow a successful handoff. Never retry installation automatically.
        setInstallResult("uncertain");
      } else {
        setError(cause instanceof Error ? cause.message : "更新请求失败，请重试");
      }
    } finally {
      if (current()) {
        scope.busy = false;
        setPending(null);
      }
    }
  }

  const readStatus = useEffectEvent(() => { void run("status"); });
  useEffect(() => {
    const scope = lifetime.current;
    scope.active = true;
    const request = scope.request;
    queueMicrotask(() => {
      if (scope.active && request === scope.request) readStatus();
    });
    return () => {
      scope.active = false;
      ++scope.request;
      scope.busy = false;
    };
  }, []);

  useEffect(() => {
    if (!state?.supported || !activePhase || pending || error || restarting) return;
    // Schedule only after the previous request settles, so slow reads cannot overlap.
    const timer = window.setTimeout(readStatus, 1500);
    return () => window.clearTimeout(timer);
  }, [state, activePhase, pending, error, restarting]);

  return { state, pending, error, installResult, activePhase, restarting, run };
}
