"use client";

import { useEffect, useState } from "react";
import { Alert, Button, Card, Space, Statistic, Typography } from "antd";
import Link from "next/link";
import { useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { AccountSetup } from "@/features/settings/SettingsPage";
import { SyncStatus } from "@/features/account/SyncStatus";
import { inboxCards } from "@/domain/chat/inboxModel";
import type { InboxOverview } from "@/types/inbox";

export function HomePage() {
  const { snapshot, blocked, suspended, generation } = useAccount();
  if ((blocked && !suspended) || !snapshot?.capabilities.read_chat) return <AccountSetup />;
  return <InboxDashboard key={`${snapshot.account.epoch}:${generation}`} />;
}

function InboxDashboard() {
  const backend = useAccountBackend();
  const { blocked, readRefreshSequence } = useAccount();
  const [overview, setOverview] = useState<InboxOverview>();
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (blocked) return;
    let cancelled = false;
    let pending = false;
    const load = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const next = await backend.getInboxOverview();
        if (!cancelled) { setOverview(next); setError(false); }
      } catch {
        if (!cancelled) setError(true);
      } finally { pending = false; }
    };
    void load();
    const timer = setInterval(() => void load(), 10_000);
    const visible = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { cancelled = true; clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [backend, blocked, readRefreshSequence, retry]);

  return <Space orientation="vertical" size="large" className="w-full">
    <Typography.Title level={3}>收件箱</Typography.Title>
    <SyncStatus />
    {error ? <Alert type="warning" showIcon title="统计读取失败，保留上次结果" action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} /> : null}
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      {inboxCards.map((item) => <Card key={item.key}>
        <Statistic title={item.label} value={overview?.counts[item.key] ?? "暂未读取"} />
        <Link href={`/chat/customer-sessions?${item.query}`}><Button className="mt-3">查看{item.label}</Button></Link>
      </Card>)}
    </div>
    <Typography.Text type="secondary">本工作台未读与待回复分别统计。历史待确认不计超时。{overview ? `当前超时阈值 ${overview.timeout_seconds / 3600} 小时 · 全部会话 ${overview.counts.total} 个 · 更新于 ${new Date(overview.updated_at * 1000).toLocaleString()}` : ""}</Typography.Text>
    <Link href="/chat/customer-sessions"><Button type="primary">前往聊天工作台</Button></Link>
  </Space>;
}
