"use client";

import { App } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "@/services/client";
import type { SystemStatusSnapshot } from "@/types/status";

export function useStatusWorkbench() {
  const { message } = App.useApp();
  const [snapshot, setSnapshot] = useState<SystemStatusSnapshot>();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const [deletingTaskId, setDeletingTaskId] = useState<string>();
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

  async function deleteTask(id: string) {
    if (deletingTaskId) return false;
    setDeletingTaskId(id);
    try {
      await backend.deleteTask(id);
      const synced = await loadSnapshot();
      if (synced) message.success("任务已删除");
      else message.warning("任务已删除，状态未同步");
      return true;
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "任务删除失败");
      return false;
    } finally {
      setDeletingTaskId(undefined);
    }
  }

  return { snapshot, loading, refreshing, creatingTask, refresh, createTestTask, deleteTask, deletingTaskId };
}
