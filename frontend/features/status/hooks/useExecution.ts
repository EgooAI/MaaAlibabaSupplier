"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "@/services/client";
import { operationErrorMessage } from "@/services/errors";
import type { ExecutionRunDetail, ExecutionRunStatus, ExecutionState } from "@/types/execution";

const POLL_MS = 1000;
const SCRIPT_KEY = "maa:execution-script";
const TERMINAL: ExecutionRunStatus[] = ["succeeded", "failed", "cancelled", "timeout", "interrupted"];

const DEFAULT_SCRIPT = `# 以当前 Windows 用户权限运行，可使用标准库与已安装依赖。
import json, platform, sys
print(json.dumps({"python": sys.version.split()[0], "platform": platform.platform()}, ensure_ascii=False))
`;

export function isExecutionTerminal(status: ExecutionRunStatus | undefined): boolean {
  return status !== undefined && TERMINAL.includes(status);
}

function newRunId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function initialScript(): string {
  try {
    return localStorage.getItem(SCRIPT_KEY) ?? DEFAULT_SCRIPT;
  } catch {
    return DEFAULT_SCRIPT;
  }
}

export function useExecution() {
  const [state, setState] = useState<ExecutionState>();
  const [detail, setDetail] = useState<ExecutionRunDetail>();
  const [script, setScript] = useState(initialScript);
  const [timeout, setTimeoutSeconds] = useState(60);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const offsets = useRef({ stdout: 0, stderr: 0 });
  const buffers = useRef({ stdout: "", stderr: "" });
  const mounted = useRef(true);

  const loadState = useCallback(async () => {
    try {
      const next = await backend.getExecutionState();
      if (!mounted.current) return;
      setState(next);
      setTimeoutSeconds((current) => (current === 60 ? next.limits.default_timeout_s : current));
      setError(undefined);
    } catch (caught) {
      if (mounted.current) setError(operationErrorMessage(caught, "远程执行状态加载失败", "请刷新页面查看是否已开启或产生任务"));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    // Deferring the first read avoids a duplicate request when StrictMode replays effects.
    queueMicrotask(() => {
      if (mounted.current) void loadState();
    });
    return () => {
      mounted.current = false;
    };
  }, [loadState]);

  useEffect(() => {
    try {
      localStorage.setItem(SCRIPT_KEY, script);
    } catch {
      // Storage may be unavailable in private modes; the editor still works.
    }
  }, [script]);

  const readNewOutput = useCallback(async (runId: string) => {
    const next = await backend.getExecutionRun(runId, {
      stdoutOffset: offsets.current.stdout, stderrOffset: offsets.current.stderr,
    });
    if (!mounted.current) return;
    // A shortened file (for example after a restart) restarts from its end to
    // avoid duplicating text the panel already holds.
    if (next.stdout_offset < offsets.current.stdout) buffers.current.stdout = "";
    if (next.stderr_offset < offsets.current.stderr) buffers.current.stderr = "";
    buffers.current = {
      stdout: buffers.current.stdout + next.stdout,
      stderr: buffers.current.stderr + next.stderr,
    };
    offsets.current = { stdout: next.stdout_offset, stderr: next.stderr_offset };
    setDetail({ ...next, stdout: buffers.current.stdout, stderr: buffers.current.stderr });
    if (isExecutionTerminal(next.status)) void loadState();
  }, [loadState]);

  const openRun = useCallback(async (runId: string) => {
    offsets.current = { stdout: 0, stderr: 0 };
    buffers.current = { stdout: "", stderr: "" };
    try {
      await readNewOutput(runId);
    } catch (caught) {
      if (mounted.current) setError(operationErrorMessage(caught, "执行记录读取失败"));
    }
  }, [readNewOutput]);

  useEffect(() => {
    if (!detail || isExecutionTerminal(detail.status)) return;
    const timer = window.setTimeout(() => {
      void readNewOutput(detail.id).catch((caught: unknown) => {
        if (mounted.current) setError(operationErrorMessage(caught, "执行输出读取失败"));
      });
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [detail, readNewOutput]);

  const toggle = useCallback(async (enabled: boolean) => {
    if (!state || busy) return;
    setBusy(true);
    try {
      const next = await backend.setExecutionEnabled(enabled, state.generation);
      if (!mounted.current) return;
      setState(next);
      setError(undefined);
    } catch (caught) {
      if (mounted.current) setError(operationErrorMessage(caught, "执行开关切换失败"));
      await loadState();
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [state, busy, loadState]);

  const submit = useCallback(async () => {
    if (!state?.enabled || busy || !script.trim()) return;
    setBusy(true);
    try {
      const submitted = await backend.submitExecutionRun({
        id: newRunId(), start_token: state.start_token, generation: state.generation,
        script, timeout_s: timeout,
      });
      if (!mounted.current) return;
      offsets.current = { stdout: submitted.stdout_offset, stderr: submitted.stderr_offset };
      buffers.current = { stdout: submitted.stdout, stderr: submitted.stderr };
      setDetail(submitted);
      setError(undefined);
      await loadState();
    } catch (caught) {
      if (mounted.current) setError(operationErrorMessage(caught, "脚本提交失败"));
      await loadState();
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [state, busy, script, timeout, loadState]);

  const cancel = useCallback(async () => {
    if (!detail || isExecutionTerminal(detail.status) || busy) return;
    setBusy(true);
    try {
      const cancelled = await backend.cancelExecutionRun(detail.id);
      if (!mounted.current) return;
      setDetail((current) => (current && current.id === cancelled.id ? { ...current, ...cancelled } : current));
      setError(undefined);
    } catch (caught) {
      if (mounted.current) setError(operationErrorMessage(caught, "停止执行失败"));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [detail, busy]);

  const refresh = useCallback(async () => {
    await loadState();
    if (detail) await openRun(detail.id);
  }, [loadState, detail, openRun]);

  return {
    state, detail, script, setScript, timeout, setTimeoutSeconds,
    loading, busy, error, toggle, submit, cancel, openRun, refresh,
  };
}
