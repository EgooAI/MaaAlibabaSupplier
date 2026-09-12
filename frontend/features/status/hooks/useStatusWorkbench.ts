"use client";

import { App } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "@/services/client";
import type { SystemStatusSnapshot } from "@/types/status";

const POLL_INTERVAL_MS = 2000;

export function useStatusWorkbench() {
  const { message } = App.useApp();
  const [snapshot, setSnapshot] = useState<SystemStatusSnapshot>();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const [testingNode, setTestingNode] = useState<string>();
  const snapshotRequestRef = useRef(0);

  const loadSnapshot = useCallback(async (initial = false) => {
    const requestId = snapshotRequestRef.current + 1;
    snapshotRequestRef.current = requestId;
    if (initial) setLoading(true);
    try {
      const nextSnapshot = await backend.getSystemStatus();
      if (snapshotRequestRef.current !== requestId) return false;
      setSnapshot(nextSnapshot);
      return true;
    } catch (error: unknown) {
      if (snapshotRequestRef.current === requestId) message.error(error instanceof Error ? error.message : "系统状态加载失败");
      return false;
    } finally {
      if (initial) setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    queueMicrotask(() => loadSnapshot(true));
  }, [loadSnapshot]);

  useEffect(() => {
    const timer = setInterval(() => {
      void backend.getSystemStatus().then(
        (nextSnapshot) => setSnapshot(nextSnapshot),
        () => undefined,
      );
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, []);

  async function refresh() {
    if (refreshing) return;
    const requestId = snapshotRequestRef.current + 1;
    snapshotRequestRef.current = requestId;
    setRefreshing(true);
    try {
      const nextSnapshot = await backend.refreshSystemStatus();
      if (snapshotRequestRef.current !== requestId) return;
      setSnapshot(nextSnapshot);
      message.success("系统状态已刷新");
    } catch (error: unknown) {
      if (snapshotRequestRef.current === requestId) message.error(error instanceof Error ? error.message : "系统状态刷新失败");
    } finally {
      setRefreshing(false);
    }
  }

  async function createTestTask() {
    if (creatingTask) return;
    setCreatingTask(true);
    try {
      await backend.createTestTask({ type: "前端连通性测试", target: "Console" });
      const synced = await loadSnapshot();
      if (synced) message.success("测试任务已创建");
      else message.warning("测试任务已创建，状态未同步");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "测试任务创建失败");
    } finally {
      setCreatingTask(false);
    }
  }

  async function runNodeTest(entry: string) {
    if (testingNode) return;
    setTestingNode(entry);
    try {
      const result = await backend.runNodeTest(entry);
      if (result.success) message.success(result.message || "节点测试通过");
      else message.error(result.message || "节点测试失败");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "节点测试失败");
    } finally {
      setTestingNode(undefined);
    }
  }

  return { snapshot, loading, refreshing, creatingTask, testingNode, refresh, createTestTask, runNodeTest };
}
