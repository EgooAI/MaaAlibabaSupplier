"use client";

import { DesktopOutlined, DownloadOutlined, PushpinFilled } from "@ant-design/icons";
import { Alert, Button, Popover, Spin } from "antd";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { useAccountBackend } from "@/features/account/AccountProvider";

export function ClientLiveView() {
  const backend = useAccountBackend();
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [sending, setSending] = useState(false);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const update = () => {
      setVisible(!document.hidden);
      if (document.hidden) { setHovered(false); setSending(false); }
    };
    update();
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);

  useEffect(() => {
    if (!visible) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const activity = await backend.getRemoteActivity();
        if (active) setSending(activity.sending);
      } catch {
        if (active) setSending(false);
      } finally {
        if (active) timer = setTimeout(() => void poll(), 250);
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [backend, visible]);

  const open = visible && (pinned || hovered || sending);
  return <Popover placement="bottomRight" trigger="hover" open={open} onOpenChange={setHovered}
    mouseEnterDelay={0.1} mouseLeaveDelay={0.2} destroyOnHidden
    content={open ? <LiveFrames /> : null}
    title={<div className="flex items-center justify-between gap-4">
      <span>客户端实况</span>
      <span className="text-xs font-normal text-slate-500">{pinned ? "常驻显示" : sending ? "正在发送消息" : "悬浮预览"}</span>
    </div>}>
    <Button icon={pinned ? <PushpinFilled /> : <DesktopOutlined />} type={pinned ? "primary" : "default"}
      aria-label="客户端实况" aria-pressed={pinned} aria-expanded={open}
      onClick={() => setPinned((value) => !value)}>
      <span className="hidden sm:inline">客户端实况</span>
    </Button>
  </Popover>;
}

function LiveFrames() {
  const backend = useAccountBackend();
  const [frame, setFrame] = useState<{ url: string; width: number; height: number }>();
  const [error, setError] = useState<string>();
  const [fps, setFps] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const active = useRef(false);
  const transfer = useRef<AbortController | undefined>(undefined);

  useEffect(() => {
    active.current = true;
    let current = true;
    let url: string | undefined;
    let etag: string | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let samples = 0;
    let sampledAt = performance.now();
    const controller = new AbortController();
    transfer.current = controller;
    const meter = setInterval(() => {
      const now = performance.now();
      setFps(Math.round(samples * 1000 / (now - sampledAt)));
      samples = 0;
      sampledAt = now;
    }, 1000);
    const poll = async () => {
      let delay = 200;
      try {
        const result = await backend.getRemoteFrame(etag, controller.signal);
        if (!current) return;
        samples += 1;
        if (result.blob) {
          const next = URL.createObjectURL(result.blob);
          if (url) URL.revokeObjectURL(url);
          url = next;
          etag = result.etag ?? undefined;
          setFrame({ url, width: result.width, height: result.height });
        }
        setError(undefined);
      } catch (reason) {
        delay = 1000;
        if (current) setError(reason instanceof Error ? reason.message : "实况画面获取失败");
      } finally {
        if (current) timer = setTimeout(() => void poll(), delay);
      }
    };
    void poll();
    return () => {
      active.current = false;
      current = false;
      controller.abort();
      clearTimeout(timer);
      clearInterval(meter);
      if (url) URL.revokeObjectURL(url);
    };
  }, [backend]);

  async function download() {
    if (downloading) return;
    setDownloading(true);
    try {
      const blob = await backend.downloadRemoteOriginal(transfer.current?.signal);
      if (!active.current) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `client-${Date.now()}.png`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      if (active.current) setError(reason instanceof Error ? reason.message : "截图下载失败");
    } finally {
      if (active.current) setDownloading(false);
    }
  }

  return <section aria-label="客户端实况小窗" className="w-[min(560px,calc(100vw-48px))] space-y-2">
    {error ? <Alert type="warning" showIcon title={error} /> : null}
    {frame ? <Image src={frame.url} width={frame.width} height={frame.height} alt="客户端实时画面"
      unoptimized draggable={false} className="block max-h-[65dvh] w-full select-none bg-black object-contain" />
      : <div className="flex h-48 items-center justify-center"><Spin /></div>}
    <div className="flex items-center justify-between">
      <span className="text-xs tabular-nums text-slate-500" title="每秒成功采样帧数，含画面未变化的帧">{fps} FPS</span>
      <Button size="small" type="text" icon={<DownloadOutlined />} loading={downloading} onClick={() => void download()}>下载截图</Button>
    </div>
  </section>;
}
