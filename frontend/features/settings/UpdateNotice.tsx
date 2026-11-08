"use client";

import { Alert, Button, Card, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { formatFullDateTime } from "@/domain/time";
import { backend } from "@/services/client";
import { parseInstallationNotice } from "@/services/updateProtocol";
import type { InstallationNotice } from "@/types/update";

const POLL_MS = 5000;

/** Displays the server-owned notice across pages until a device acknowledges it. */
export function UpdateNotice() {
  const [notice, setNotice] = useState<InstallationNotice | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const acknowledge = useRef<((id: string) => Promise<void>) | null>(null);

  useEffect(() => {
    let active = true;
    let reading = false;
    let acknowledging = false;
    let revision = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function schedule() {
      clearTimeout(timer);
      if (active) timer = setTimeout(() => void read(), POLL_MS);
    }

    async function read() {
      if (!active || reading || acknowledging) return;
      if (document.visibilityState === "hidden") { schedule(); return; }
      reading = true;
      const started = revision;
      try {
        const value = parseInstallationNotice(await backend.getInstallationNotice());
        if (active && started === revision) setNotice(value);
      } catch {
        // Keeps the last notice through a restart or a temporary read failure.
      } finally {
        reading = false;
        schedule();
      }
    }

    acknowledge.current = async (id) => {
      if (!active || acknowledging) return;
      acknowledging = true;
      ++revision;
      setPending(true);
      setError(null);
      try {
        const value = parseInstallationNotice(await backend.acknowledgeInstallation(id));
        if (active) setNotice(value);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "确认未保存，请重试");
      } finally {
        acknowledging = false;
        if (active) setPending(false);
        schedule();
      }
    };
    const resume = () => { void read(); };
    queueMicrotask(resume);
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      active = false;
      acknowledge.current = null;
      clearTimeout(timer);
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, []);

  if (!notice) return null;
  return <div role="status" aria-live="polite" className="fixed bottom-4 right-4 z-[1100] w-[min(24rem,calc(100vw-2rem))]">
    <Card title="程序更新完成" size="small" className="shadow-xl">
      <Space orientation="vertical" className="w-full">
        <Typography.Text>已安装 {notice.version} 并重新启动程序。</Typography.Text>
        <Typography.Text>安装执行时间：{formatFullDateTime(notice.executed_at)}</Typography.Text>
        <Typography.Text type="secondary">点击“知道了”后，所有设备将同步关闭此通知。</Typography.Text>
        {error ? <Alert type="error" title={error} /> : null}
        <Button type="primary" loading={pending} onClick={() => void acknowledge.current?.(notice.id)}>知道了</Button>
      </Space>
    </Card>
  </div>;
}
