"use client";

import { App } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "@/services/client";
import { accountSession } from "@/services/accountSession";
import { operationErrorMessage } from "@/services/errors";
import type { NodeTestEntry, SystemStatusSnapshot } from "@/types/status";

const POLL_INTERVAL_MS = 2000;

export function useStatusWorkbench() {
  const { message } = App.useApp();
  const [snapshot, setSnapshot] = useState<SystemStatusSnapshot>();
  const [loading, setLoading] = useState(true);
  const [creatingTask, setCreatingTask] = useState(false);
  const [testingNode, setTestingNode] = useState<NodeTestEntry>();
  const [lastFailure, setLastFailure] = useState<{ at: number; message: string }>();
  const inFlight = useRef<Promise<boolean> | null>(null);
  const lifecycle = useRef(0);

  const loadSnapshot = useCallback(() => {
    if (inFlight.current) return inFlight.current;
    const generation = lifecycle.current;
    const accountGeneration = accountSession.get().generation;
    const current = () => lifecycle.current === generation && accountSession.get().generation === accountGeneration;
    const pending = (async () => {
      try {
        const nextSnapshot = await backend.getSystemStatus();
        if (!current()) return false;
        setSnapshot(nextSnapshot);
        setLastFailure(undefined);
        return true;
      } catch (error: unknown) {
        if (current()) setLastFailure({ at: Date.now(), message: operationErrorMessage(error, "系统状态加载失败") });
        return false;
      } finally {
        if (current()) setLoading(false);
      }
    })();
    inFlight.current = pending;
    void pending.finally(() => { if (inFlight.current === pending) inFlight.current = null; });
    return pending;
  }, []);

  useEffect(() => {
    const generation = lifecycle.current;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (cancelled) return;
      if (!document.hidden) await loadSnapshot();
      if (!cancelled) timer = setTimeout(poll, POLL_INTERVAL_MS);
    };
    let accountGeneration = accountSession.get().generation;
    const unsubscribe = accountSession.subscribe(() => {
      const next = accountSession.get().generation;
      if (next === accountGeneration) return;
      accountGeneration = next;
      setSnapshot(undefined);
      setLastFailure(undefined);
      setLoading(true);
    });
    queueMicrotask(poll);
    return () => { cancelled = true; lifecycle.current = generation + 1; clearTimeout(timer); unsubscribe(); };
  }, [loadSnapshot]);

  async function createTestTask() {
    if (creatingTask) return;
    setCreatingTask(true);
    try {
      await backend.createTestTask({ type: "前端连通性测试", target: "Console" });
      const synced = await loadSnapshot();
      if (synced) message.success("测试任务已创建");
      else message.warning("测试任务已创建，状态未同步");
    } catch (error: unknown) {
      message.error(operationErrorMessage(error, "测试任务创建失败", "请先查看任务列表，勿立即重复提交"));
    } finally {
      setCreatingTask(false);
    }
  }

  async function runNodeTest(entry: NodeTestEntry) {
    if (testingNode) return;
    setTestingNode(entry);
    try {
      await backend.runNodeTest(entry);
      message.info("节点测试任务已提交排队，请在状态页任务列表查看结果（每 2 秒自动更新）");
    } catch (error: unknown) {
      message.error(operationErrorMessage(error, "节点测试提交失败", "请先查看任务列表，勿立即重复提交"));
    } finally {
      setTestingNode(undefined);
    }
  }

  return { snapshot, loading, lastFailure, creatingTask, testingNode, createTestTask, runNodeTest };
}
