"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAccount, useAccountBackend, AccountChangedError } from "@/features/account/AccountProvider";
import type { OutboxTask } from "@/types/chatOperations";
import { intentKey, loadIntents, persistIntents, type PendingIntent } from "./intentStorage";
import { canCancel, canRetry, isTerminal } from "./outboxModel";
import { captureAccount } from "@/services/accountSession";
import { ApiError } from "@/services/httpAdapter";

export function useOutbox(sid: string) {
  const backend = useAccountBackend();
  const { snapshot, blocked } = useAccount();
  const storageKey = intentKey(snapshot!.account, sid);
  const [tasks, setTasks] = useState<OutboxTask[]>([]);
  const [intents, setIntents] = useState<PendingIntent[]>([]);
  const [error, setError] = useState<string>();
  const [storageError, setStorageError] = useState(false);
  const [busy, setBusy] = useState(false);
  const active = useRef(true);
  const locked = useRef(false);
  const polling = useRef(false);
  const knownTasks = useRef(new Map<string, OutboxTask>());

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  const accept = useCallback((incoming: OutboxTask[], remember = false) => {
    if (!active.current) return;
    for (const task of incoming) {
      if (String(task.conversation_id) !== sid) continue;
      const old = knownTasks.current.get(task.id);
      if (!old || old.version <= task.version) knownTasks.current.set(task.id, task);
    }
    setTasks([...knownTasks.current.values()].sort((a, b) => b.created_at - a.created_at));
    try {
      const saved = loadIntents(localStorage, storageKey);
      const byKey = new Map(saved.map((intent) => [intent.key, intent]));
      const explicit = new Set(remember ? incoming.map((task) => task.id) : []);
      const updates: PendingIntent[] = [];
      for (const task of knownTasks.current.values()) {
        const intent = byKey.get(task.idempotency_key);
        if (intent || explicit.has(task.id)) updates.push({ ...(intent ?? { key: task.idempotency_key, content: task.content, action: task.action }), taskId: task.id });
      }
      setIntents(updates.length ? persistIntents(localStorage, storageKey, updates) : saved);
    } catch { setStorageError(true); }
  }, [sid, storageKey]);

  const readStorage = useCallback(() => {
    try {
      const saved = loadIntents(localStorage, storageKey);
      if (active.current) setIntents(saved);
      return saved;
    } catch {
      if (active.current) setStorageError(true);
      throw new Error("浏览器无法保存提交记录。已禁止提交，请恢复浏览器存储后刷新任务；草稿仍保留。");
    }
  }, [storageKey]);

  const refresh = useCallback(async (checkStorage = false) => {
    if (polling.current || blocked) return;
    polling.current = true;
    try {
      const ticket = captureAccount();
      let saved: PendingIntent[] = [];
      try {
        saved = readStorage();
        if (checkStorage) {
          const encoded = JSON.stringify(saved);
          localStorage.setItem(storageKey, encoded);
          if (localStorage.getItem(storageKey) !== encoded) throw new Error("Storage verification failed");
          setStorageError(false);
        }
      } catch { setStorageError(true); }
      const result = await backend.listOutbox(sid);
      if (!active.current) return;
      ticket.assertCurrent();
      accept(result);
      const recentIds = new Set(result.filter((task) => String(task.conversation_id) === sid).map((task) => task.id));
      const targets = new Map<string, string>();
      for (const task of knownTasks.current.values()) {
        if (!recentIds.has(task.id) && (!isTerminal(task) || checkStorage)) targets.set(task.id, task.idempotency_key);
      }
      for (const intent of saved) {
        if (intent.taskId && !recentIds.has(intent.taskId) && !knownTasks.current.has(intent.taskId)) targets.set(intent.taskId, intent.key);
      }
      // Recent lists are capped by creation time. Retried older tasks need direct reads.
      const reads = await Promise.allSettled([...targets].map(async ([id, key]) => {
        const task = await backend.getOutbox(id);
        if (task.id !== id || task.idempotency_key !== key || String(task.conversation_id) !== sid) throw new Error("任务响应不匹配");
        return task;
      }));
      if (!active.current) return;
      ticket.assertCurrent();
      accept(reads.flatMap((read) => read.status === "fulfilled" ? [read.value] : []));
      if (reads.some((read) => read.status === "rejected")) throw new Error("任务补充读取失败");
      setError(undefined);
    } catch (err) {
      if (active.current && !(err instanceof AccountChangedError)) setError("任务读取失败，保留已有记录；请刷新任务核对结果。");
    } finally { polling.current = false; }
  }, [accept, backend, blocked, readStorage, sid, storageKey]);

  useEffect(() => {
    const visibleRefresh = () => { if (!document.hidden) void refresh(); };
    visibleRefresh();
    const timer = setInterval(visibleRefresh, 2000);
    document.addEventListener("visibilitychange", visibleRefresh);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", visibleRefresh); };
  }, [refresh]);

  async function submit(content: string, action: PendingIntent["action"], fixedIntent?: PendingIntent) {
    if (locked.current || blocked || !snapshot?.capabilities.operate_client || !content.trim()) return false;
    locked.current = true;
    setBusy(true);
    let persisted = false;
    try {
      readStorage();
      // A deliberately new send owns one key for the entire dialog lifetime.
      if (fixedIntent) {
        if (fixedIntent.content !== content || fixedIntent.action !== action) throw new Error("提交内容与原意图不匹配");
        try { setIntents(persistIntents(localStorage, storageKey, [fixedIntent])); }
        catch { setStorageError(true); throw new Error("浏览器无法保存提交记录。已禁止提交，请恢复浏览器存储后刷新任务；草稿仍保留。"); }
        persisted = true;
      }
      // Reconcile before submitting, including after a lost POST response or reload.
      const current = await backend.listOutbox(sid);
      if (!active.current) return false;
      accept(current);
      const saved = readStorage();
      const matching = current.filter((task) => String(task.conversation_id) === sid && task.content === content && task.action === action).sort((a, b) => b.created_at - a.created_at);
      const prior = fixedIntent ? saved.find((item) => item.key === fixedIntent.key) ?? fixedIntent : saved.findLast((item) => item.content === content && item.action === action);
      let existing = prior ? current.find((task) => task.idempotency_key === prior.key && String(task.conversation_id) === sid) : matching[0];
      if (!existing && prior?.taskId) {
        existing = await backend.getOutbox(prior.taskId);
        if (!active.current) return false;
        if (existing.id !== prior.taskId || existing.idempotency_key !== prior.key || String(existing.conversation_id) !== sid) throw new Error("任务响应不匹配");
        accept([existing]);
      }
      const intent: PendingIntent = prior ?? {
        key: existing ? existing.idempotency_key : crypto.randomUUID(), content, action,
      };
      try {
        setIntents(persistIntents(localStorage, storageKey, [intent]));
        setStorageError(false);
      } catch {
        setStorageError(true);
        throw new Error("浏览器无法保存提交记录。已禁止提交，请恢复浏览器存储后刷新任务；草稿仍保留。");
      }
      persisted = true;
      if (existing) { accept([existing], true); setError(undefined); return true; }
      const result = await backend.sendMessage({ conversationId: sid, content: intent.content, action: intent.action, idempotency_key: intent.key });
      if (!active.current) return false;
      if (result.outbox.idempotency_key !== intent.key || String(result.outbox.conversation_id) !== sid) throw new Error("任务响应不匹配");
      accept([result.outbox], true);
      setError(undefined);
      return true;
    } catch (err) {
      if (active.current && !(err instanceof AccountChangedError)) setError(persisted
        ? err instanceof ApiError && err.status === 409
          ? `提交冲突（409）：${err.message}。原幂等记录已保留，请稍后刷新或恢复原提交；任务状态以后台记录为准。`
          : "提交结果未知，幂等记录已保存。请刷新任务；未查到时可恢复原提交，不会新建发送。草稿已保留。"
        : err instanceof Error ? err.message : "提交失败，草稿已保留");
      return false;
    } finally {
      locked.current = false;
      if (active.current) setBusy(false);
    }
  }

  async function mutate(task: OutboxTask, action: "cancel" | "retry") {
    if (locked.current || blocked || (action === "retry" && !snapshot?.capabilities.operate_client)) return;
    if (action === "cancel" ? !canCancel(task) : !canRetry(task)) return;
    locked.current = true;
    setBusy(true);
    try {
      accept([task], true);
      const result = action === "cancel" ? await backend.cancelOutbox(task.id, task.version) : await backend.retryOutbox(task.id, task.version);
      accept([result], true);
    } catch (err) {
      if (active.current && !(err instanceof AccountChangedError)) setError("任务状态可能已变化，请刷新任务后核对；不会自动重试操作。");
    } finally {
      locked.current = false;
      if (active.current) { setBusy(false); void refresh(); }
    }
  }

  return { tasks, intents, error, storageError, busy, submit, mutate, refresh, accept, backend };
}
