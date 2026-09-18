"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, App, Button, Card, InputNumber, Space, Typography } from "antd";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";

export function InboxSettingsCard() {
  const { snapshot, blocked, suspended, generation } = useAccount();
  if ((blocked && !suspended) || !snapshot?.account.self_ali_id) return null;
  return <InboxSettingsEditor key={`${snapshot.account.epoch}:${generation}`} />;
}

function InboxSettingsEditor() {
  const backend = useAccountBackend();
  const { blocked, refreshReads } = useAccount();
  const { message } = App.useApp();
  const [hours, setHours] = useState<number | null>(null);
  const [saved, setSaved] = useState<number>();
  const [error, setError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [retry, setRetry] = useState(0);
  const request = useRef(0);
  const busy = useRef(false);
  useEffect(() => {
    if (blocked) return;
    const id = ++request.current;
    void backend.getInboxSettings().then((value) => {
      if (id !== request.current) return;
      setHours(value.timeout_seconds / 3600);
      setSaved(value.timeout_seconds / 3600);
      setError(false);
    }).catch(() => { if (id === request.current) setError(true); });
    return () => {
      // Invalidate the latest read or save, including operations started after mount.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      ++request.current;
    };
  }, [backend, blocked, retry]);

  async function save() {
    if (hours === null || hours < 1 || hours > 168 || busy.current) return;
    busy.current = true;
    const id = ++request.current;
    setSaving(true);
    try {
      const value = await backend.saveInboxSettings(Math.round(hours * 3600));
      if (id !== request.current) return;
      setHours(value.timeout_seconds / 3600);
      setSaved(value.timeout_seconds / 3600);
      setError(false);
      refreshReads();
      message.success("回复超时设置已保存");
    } catch (error) {
      if (id === request.current && !(error instanceof AccountChangedError)) message.error("回复超时设置保存失败，请重试");
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }

  return <Card title="回复超时" className="w-full max-w-md">
    <Space orientation="vertical" className="w-full">
      <Typography.Text type="secondary">默认 24 小时，可设 1 至 168 小时。设置按当前账号和数据目录保存；历史待确认不计超时。</Typography.Text>
      {error ? <Alert type="warning" title="设置读取失败" action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} /> : null}
      <Space wrap>
        <InputNumber aria-label="回复超时小时数" min={1} max={168} value={hours} onChange={setHours} disabled={blocked || saving || saved === undefined} suffix="小时" />
        <Button type="primary" loading={saving} disabled={blocked || hours === null || !Number.isFinite(hours) || hours < 1 || hours > 168 || hours === saved || saved === undefined} onClick={() => void save()}>保存回复超时</Button>
      </Space>
    </Space>
  </Card>;
}
