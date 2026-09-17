"use client";

import { Alert, Button, Modal, Space, Typography } from "antd";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import type { OutboxTask } from "@/types/chatOperations";
import type { OperationsBackend } from "@/services/interfaces";
import { AccountChangedError } from "@/services/accountSession";
import { frameFresh } from "./outboxModel";

export function ScreenshotConfirmation({ task, seller, recipient, canOperate, backend, onTask, onClose }: {
  task: OutboxTask; seller: string; recipient: string; canOperate: boolean; backend: OperationsBackend;
  onTask: (task: OutboxTask) => void; onClose: () => void;
}) {
  const [frame, setFrame] = useState<{ task: OutboxTask; url: string }>();
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [fullSize, setFullSize] = useState(false);
  const locked = useRef(false);
  const active = useRef(true);
  const updateTask = useEffectEvent(onTask);
  const { id, version, screenshot_id: screenshotId } = task;

  useEffect(() => {
    let disposed = false;
    let url: string | undefined;
    let expiry: ReturnType<typeof setTimeout> | undefined;
    active.current = true;
    async function load() {
      setFrame(undefined);
      setLoaded(false);
      setError(undefined);
      try {
        const current = await backend.getOutbox(id);
        if (disposed) return;
        if (current.id !== id || current.version !== version || current.screenshot_id !== screenshotId || current.status !== "awaiting_confirmation") {
          updateTask(current);
          setError("任务或截图已变化，请重新查看并确认。");
          return;
        }
        if (!frameFresh(current)) throw new Error("截图已过期或不可用（有效期 120 秒）。请刷新任务；若仍过期，可取消该任务后新建，或在安全失败状态下重试并重新截图。");
        const blob = await backend.getOutboxScreenshot(id, current.screenshot_id!, current.version);
        if (disposed) return;
        if (!frameFresh(current)) throw new Error("截图已过期，请刷新任务。");
        url = URL.createObjectURL(blob);
        setFrame({ task: current, url });
        expiry = setTimeout(() => {
          if (url) URL.revokeObjectURL(url);
          url = undefined;
          setFrame(undefined);
          setLoaded(false);
          setError("截图已过期（120 秒），请刷新任务后重新核对。读取不会自动重新截图或发送。");
        }, current.screenshot_at! * 1000 + 120_000 - Date.now());
      } catch (err) {
        if (!disposed && !(err instanceof AccountChangedError)) setError(err instanceof Error ? err.message : "截图加载失败，请刷新任务。");
      }
    }
    void load();
    return () => {
      disposed = true;
      active.current = false;
      if (expiry) clearTimeout(expiry);
      if (url) URL.revokeObjectURL(url);
    };
  }, [backend, id, version, screenshotId, refresh]);

  async function confirm() {
    if (locked.current || !canOperate || !loaded || !frame || !frameFresh(frame.task)) return;
    locked.current = true;
    setBusy(true);
    setLoaded(false);
    try {
      const current = await backend.getOutbox(id);
      if (!active.current) return;
      if (current.id !== id || current.version !== frame.task.version || current.screenshot_id !== frame.task.screenshot_id || !frameFresh(current)) {
        onTask(current);
        setError("任务或截图已变化，请刷新截图并重新确认。");
        return;
      }
      const result = await backend.confirmOutbox(id, frame.task.version, frame.task.screenshot_id!);
      if (active.current) onTask(result);
    } catch (err) {
      if (active.current && !(err instanceof AccountChangedError)) setError("确认结果尚未核实，请刷新任务。不会自动重复确认。");
    } finally {
      locked.current = false;
      if (active.current) setBusy(false);
    }
  }

  const displayed = frame?.task ?? task;
  return <Modal title="根据客户端截图二次确认联系人" open width={960} onCancel={onClose} footer={<Space wrap>
    <Button onClick={onClose}>稍后确认</Button>
    <Button disabled={busy} onClick={() => setRefresh((value) => value + 1)}>刷新任务与截图</Button>
    <Button type="primary" disabled={!canOperate || !loaded || !frame || !frameFresh(frame.task)} loading={busy} onClick={() => void confirm()}>{displayed.action === "send" ? "确认该联系人，发送" : "确认该联系人，仅填入"}</Button>
  </Space>}>
    <Typography.Paragraph>卖家：{seller} · 收件人：{recipient} · 登录 ID：{displayed.login_id}</Typography.Paragraph>
    <Typography.Paragraph>请在实际截图中核对当前聊天联系人。搜索结果需要人工核对；确认只适用于这张截图和以下已提交内容。</Typography.Paragraph>
    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words">{displayed.content}</pre>
    {error && <Alert type="warning" title={error} />}
    {!frame && !error && <Typography.Text>正在读取任务和截图…</Typography.Text>}
    {frame && <>
      <Typography.Paragraph type="secondary">截图时间：{new Date(frame.task.screenshot_at! * 1000).toLocaleString()} · 有效期 120 秒</Typography.Paragraph>
      <Button size="small" onClick={() => setFullSize((value) => !value)}>{fullSize ? "适应宽度" : "查看原尺寸"}</Button>
      <div className="mt-2 max-h-[60vh] overflow-auto">
        {/* Authenticated PNG bytes must never be passed through an image proxy or cache. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={frame.url} alt="客户端当前联系人截图" className={fullSize ? "h-auto max-w-none" : "h-auto max-w-full"} onLoad={() => setLoaded(true)} onError={() => { setLoaded(false); setError("截图显示失败，无法确认。请刷新任务与截图。"); }} />
      </div>
    </>}
  </Modal>;
}
