"use client";

import { Button, Card, Space, Typography } from "antd";
import { useEffect, useState } from "react";

const POLL_MS = 30_000;

function parseDeployedBuildId(value: unknown): string | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const buildId = (value as Record<string, unknown>).build_id;
  return typeof buildId === "string" && buildId.length > 0 && buildId.length <= 128 ? buildId : null;
}

/** Prompts a page that kept an older frontend loaded across an installation. */
export function BuildMismatchNotice({
  loadedBuildId = process.env.NEXT_PUBLIC_APP_BUILD ?? "",
  reload = () => window.location.reload(),
}: {
  loadedBuildId?: string;
  reload?: () => void;
}) {
  const [mismatch, setMismatch] = useState(false);

  useEffect(() => {
    if (!loadedBuildId) return;
    let active = true;
    let reading = false;
    let found = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function schedule() {
      clearTimeout(timer);
      if (active && !found) timer = setTimeout(() => void read(), POLL_MS);
    }

    async function read() {
      if (!active || reading || found) return;
      if (document.visibilityState === "hidden") { schedule(); return; }
      reading = true;
      try {
        const response = await fetch("/build-id.json", { cache: "no-store" });
        if (!response.ok) return;
        const deployed = parseDeployedBuildId(await response.json());
        if (active && deployed && deployed !== loadedBuildId) {
          found = true;
          setMismatch(true);
        }
      } catch {
        // The backend may still be restarting after installation; retry next time.
      } finally {
        reading = false;
        schedule();
      }
    }

    const resume = () => { if (!found) void read(); };
    queueMicrotask(resume);
    window.addEventListener("focus", resume);
    window.addEventListener("pageshow", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      active = false;
      clearTimeout(timer);
      window.removeEventListener("focus", resume);
      window.removeEventListener("pageshow", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [loadedBuildId]);

  if (!mismatch) return null;
  return <div role="alert">
    <Card title="程序已更新" size="small" className="shadow-xl">
      <Space orientation="vertical" className="w-full">
        <Typography.Text>当前页面仍在运行旧版本，刷新后使用新版本。</Typography.Text>
        <Button type="primary" onClick={reload}>刷新页面</Button>
      </Space>
    </Card>
  </div>;
}
