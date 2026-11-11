"use client";

import { DownloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Input, Space, Spin, Typography } from "antd";
import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAccount, useAccountBackend } from "@/features/account/AccountProvider";

type Point = { x: number; y: number; at: number };

export default function RemotePage() {
  const { snapshot, blocked } = useAccount();
  const backend = useAccountBackend();
  const [sessionId, setSessionId] = useState<string>();
  const [sessionEpoch, setSessionEpoch] = useState<string | undefined>(undefined);
  const [imageUrl, setImageUrl] = useState<string>();
  const [imageEpoch, setImageEpoch] = useState<string | undefined>(undefined);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [error, setError] = useState<string>();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [originalBusy, setOriginalBusy] = useState(false);
  const [stopped, setStopped] = useState(false);
  const etag = useRef<string | undefined>(undefined);
  const imageUrlRef = useRef<string | undefined>(undefined);
  const pointer = useRef<Point | undefined>(undefined);
  const activeSessionId = sessionEpoch === snapshot?.account.epoch ? sessionId : undefined;

  const close = useCallback(async (id?: string) => {
    if (id) await backend.closeRemoteSession(id).catch(() => undefined);
  }, [backend]);

  useEffect(() => {
    let active = true;
    let currentId: string | undefined;
    let timer: ReturnType<typeof setTimeout>;
    etag.current = undefined;
    const updateFrame = async () => {
      if (!active || !currentId) return;
      try {
        const frame = await backend.getRemoteFrame(currentId, etag.current);
        if (!active) return;
        if (frame.blob) {
          const nextUrl = URL.createObjectURL(frame.blob);
          if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
          imageUrlRef.current = nextUrl;
          setImageUrl(nextUrl);
          setImageEpoch(snapshot?.account.epoch);
          setSize({ width: frame.width, height: frame.height });
          etag.current = frame.etag ?? undefined;
        }
        setError(undefined);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "截图获取失败");
      } finally {
        if (active) timer = setTimeout(() => void updateFrame(), 400);
      }
    };
    if (stopped || blocked || !snapshot?.capabilities.operate_client) return;
    void backend.createRemoteSession().then(({ session_id }) => {
      currentId = session_id;
      if (!active) { void close(session_id); return; }
      setSessionId(session_id);
      setSessionEpoch(snapshot?.account.epoch);
      void updateFrame();
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "远控会话创建失败");
    });
    return () => {
      active = false;
      clearTimeout(timer);
      if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
      if (currentId) void close(currentId);
    };
  }, [backend, blocked, stopped, snapshot?.account.epoch, snapshot?.capabilities.operate_client, close]);

  const point = (event: React.PointerEvent<HTMLImageElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(size.width - 1, Math.round((event.clientX - rect.left) * size.width / rect.width))),
      y: Math.max(0, Math.min(size.height - 1, Math.round((event.clientY - rect.top) * size.height / rect.height))),
    };
  };
  const sendAction = async (action: Record<string, unknown>) => {
    if (!activeSessionId || busy) return;
    setBusy(true);
    try { await backend.remoteAction({ session_id: activeSessionId, ...action } as Parameters<typeof backend.remoteAction>[0]); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
    finally { setBusy(false); }
  };
  const submitText = () => {
    if (!text.trim()) return;
    void sendAction({ action: "type", text });
    setText("");
  };
  const downloadOriginal = async () => {
    if (!activeSessionId || originalBusy) return;
    setOriginalBusy(true);
    try {
      const blob = await backend.downloadRemoteOriginal(activeSessionId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "client-screenshot.png";
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "原图下载失败");
    } finally {
      setOriginalBusy(false);
    }
  };

  return <Card title="客户端远控" extra={<Typography.Text type="secondary">{activeSessionId ? "已连接" : "正在连接"}</Typography.Text>}>
    <Space direction="vertical" className="w-full" size="middle">
      <Typography.Text type="secondary">点击或拖动客户端画面进行操作。长按约 600 毫秒触发。</Typography.Text>
      {error ? <Alert type="warning" showIcon title={error} /> : null}
      {imageUrl && imageEpoch === snapshot?.account.epoch ? <Image
        src={imageUrl} alt="客户端实时画面" width={size.width} height={size.height} unoptimized draggable={false}
        className="mx-auto block max-h-[70vh] max-w-full touch-none select-none border bg-black object-contain"
        onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); pointer.current = { ...point(event), at: Date.now() }; }}
        onPointerUp={(event) => {
          const start = pointer.current; pointer.current = undefined;
          if (!start) return;
          const end = point(event);
          const elapsed = Date.now() - start.at;
          if (elapsed >= 550) void sendAction({ action: "long_press", x: start.x, y: start.y, duration: Math.min(elapsed, 5000) });
          else if (Math.hypot(end.x - start.x, end.y - start.y) > 8) void sendAction({ action: "drag", x1: start.x, y1: start.y, x2: end.x, y2: end.y, duration: Math.min(5000, Math.max(100, elapsed)) });
          else void sendAction({ action: "click", x: end.x, y: end.y });
        }}
      /> : <div className="flex min-h-64 items-center justify-center"><Spin /></div>}
      <Space.Compact className="w-full">
        <Input value={text} onChange={(event) => setText(event.target.value)} onPressEnter={submitText} placeholder="输入要键入到客户端的文本" maxLength={4000} />
        <Button type="primary" disabled={!activeSessionId || busy || !text.trim()} onClick={submitText}>键入</Button>
      </Space.Compact>
      <Button icon={<DownloadOutlined />} disabled={!activeSessionId || originalBusy} loading={originalBusy} onClick={() => void downloadOriginal()}>下载截图 PNG</Button>
      <Button danger disabled={!activeSessionId} onClick={() => { void close(activeSessionId); setSessionId(undefined); setStopped(true); }}>结束远控</Button>
    </Space>
  </Card>;
}
