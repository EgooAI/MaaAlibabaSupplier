"use client";

import { App } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { backend } from "@/services/client";
import { accountSession, AccountChangedError } from "@/services/accountSession";
import { operationErrorMessage } from "@/services/errors";
import type { NodeTestEntry, SystemStatusSnapshot } from "@/types/status";

const POLL_INTERVAL_MS = 2000;

interface WorkbenchScope {
  generation: number;
  isCurrent: () => boolean;
  refresh: () => Promise<boolean>;
  creatingTask: boolean;
  testingNode: boolean;
  sweepingCards: boolean;
}

export function useStatusWorkbench() {
  const { message } = App.useApp();
  const [snapshot, setSnapshot] = useState<SystemStatusSnapshot>();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const [testingNode, setTestingNode] = useState<NodeTestEntry>();
  const [sweepingCards, setSweepingCards] = useState(false);
  const [lastFailure, setLastFailure] = useState<{ at: number; message: string }>();
  const lifecycle = useRef<WorkbenchScope | null>(null);
  const refresh = useCallback(() => lifecycle.current?.refresh() ?? Promise.resolve(false), []);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startAccount = () => {
      clearTimeout(timer);
      const account = accountSession.get();
      const epoch = account.snapshot?.account.epoch;
      // Each account owns its pending read and action locks, even while old work settles.
      let inFlight: Promise<boolean> | null = null;
      const scope: WorkbenchScope = {
        generation: account.generation,
        isCurrent: () => !cancelled && lifecycle.current === scope && accountSession.get().generation === scope.generation,
        creatingTask: false,
        testingNode: false,
        sweepingCards: false,
        refresh() {
          if (!scope.isCurrent() || document.hidden) return Promise.resolve(false);
          if (inFlight) return inFlight;
          clearTimeout(timer);
          setRefreshing(true);
          const pending = (async () => {
            try {
              const nextSnapshot = await backend.getSystemStatus();
              if (!scope.isCurrent()) return false;
              if (epoch !== undefined && nextSnapshot.context?.epoch !== epoch) throw new AccountChangedError();
              setSnapshot(nextSnapshot);
              setLastFailure(undefined);
              return true;
            } catch (error: unknown) {
              if (scope.isCurrent()) setLastFailure({ at: Date.now(), message: operationErrorMessage(error, "系统状态加载失败") });
              return false;
            } finally {
              if (scope.isCurrent()) {
                setLoading(false);
                setRefreshing(false);
              }
            }
          })();
          inFlight = pending;
          void pending.then(() => {
            if (!scope.isCurrent()) return;
            inFlight = null;
            if (!document.hidden) timer = setTimeout(() => { void scope.refresh(); }, POLL_INTERVAL_MS);
          });
          return pending;
        },
      };
      lifecycle.current = scope;
      setSnapshot(undefined);
      setLastFailure(undefined);
      setLoading(true);
      setRefreshing(false);
      setCreatingTask(false);
      setTestingNode(undefined);
      setSweepingCards(false);
      void scope.refresh();
    };
    const unsubscribe = accountSession.subscribe(() => {
      if (accountSession.get().generation !== lifecycle.current?.generation) startAccount();
    });
    const onVisibility = () => {
      if (document.hidden) clearTimeout(timer);
      else void lifecycle.current?.refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onVisibility);
    queueMicrotask(() => { if (!cancelled && !lifecycle.current) startAccount(); });
    return () => {
      cancelled = true;
      lifecycle.current = null;
      clearTimeout(timer);
      unsubscribe();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, []);

  async function createTestTask() {
    const scope = lifecycle.current;
    if (!scope?.isCurrent() || scope.creatingTask) return;
    scope.creatingTask = true;
    setCreatingTask(true);
    try {
      await backend.createTestTask({ type: "前端连通性测试", target: "Console" });
      if (!scope.isCurrent()) return;
      const synced = await scope.refresh();
      if (!scope.isCurrent()) return;
      if (synced) message.success("测试任务已创建");
      else message.warning("测试任务已创建，状态未同步");
    } catch (error: unknown) {
      if (scope.isCurrent()) message.error(operationErrorMessage(error, "测试任务创建失败", "请先查看任务列表，勿立即重复提交"));
    } finally {
      if (scope.isCurrent()) {
        scope.creatingTask = false;
        setCreatingTask(false);
      }
    }
  }

  async function runNodeTest(entry: NodeTestEntry) {
    const scope = lifecycle.current;
    if (!scope?.isCurrent() || scope.testingNode) return;
    scope.testingNode = true;
    setTestingNode(entry);
    try {
      await backend.runNodeTest(entry);
      if (scope.isCurrent()) message.info("节点测试任务已提交排队，请在状态页任务列表查看结果（每 2 秒自动更新）");
    } catch (error: unknown) {
      if (scope.isCurrent()) message.error(operationErrorMessage(error, "节点测试提交失败", "请先查看任务列表，勿立即重复提交"));
    } finally {
      if (scope.isCurrent()) {
        scope.testingNode = false;
        setTestingNode(undefined);
      }
    }
  }

  async function sweepCards() {
    const scope = lifecycle.current;
    if (!scope?.isCurrent() || scope.sweepingCards) return;
    scope.sweepingCards = true;
    setSweepingCards(true);
    try {
      await backend.sweepCards();
      if (!scope.isCurrent()) return;
      const synced = await scope.refresh();
      if (!scope.isCurrent()) return;
      if (synced) message.info("卡片扫描已触发，进度见「卡片清扫服务」（每 2 秒自动更新）。历史卡片可能无法富化。");
      else message.warning("卡片扫描已触发，状态未同步");
    } catch (error: unknown) {
      if (scope.isCurrent()) message.error(operationErrorMessage(error, "卡片扫描触发失败"));
    } finally {
      if (scope.isCurrent()) {
        scope.sweepingCards = false;
        setSweepingCards(false);
      }
    }
  }

  return { snapshot, loading, refreshing, refresh, lastFailure, creatingTask, testingNode, sweepingCards, createTestTask, runNodeTest, sweepCards };
}
