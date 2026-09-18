"use client";

import { ReloadOutlined } from "@ant-design/icons";
import { App, Button, Space, Tag, Tooltip, Typography } from "antd";
import { AccountChangedError, useAccount } from "./AccountProvider";

function time(value: number | null) {
  return value == null ? "尚无记录" : new Date(value * 1000).toLocaleString();
}

export function SyncStatus({ refreshError = false, refreshPending = false, buttonLabel = "刷新聊天", compact = false }: { refreshError?: boolean; refreshPending?: boolean; buttonLabel?: string; compact?: boolean }) {
  const { snapshot, blocked, syncBusy, requestSync } = useAccount();
  const { message } = App.useApp();
  if (!snapshot) return null;
  const source = snapshot.source;
  const failed = Boolean(source.last_error);
  const syncing = source.syncing || source.pending;
  const label = blocked ? "连接中断，身份待确认" : refreshError ? (refreshPending ? "聊天读取失败，正在重试" : "聊天读取失败，等待重试") : refreshPending ? "正在刷新聊天" : failed ? "同步失败" : syncing ? "同步中" : source.freshness === "stale" ? "存档可能过期" : "聊天已更新";
  const submit = async () => {
    try {
      await requestSync();
      message.info("同步请求已提交，完成情况请查看同步状态");
    } catch (error) {
      if (!(error instanceof AccountChangedError)) message.error(error instanceof Error ? error.message : "同步请求提交失败");
    }
  };
  return (
    <Space wrap size="small" role="status">
      <Tag color={blocked || failed || refreshError ? "error" : syncing || refreshPending ? "processing" : source.freshness === "stale" ? "warning" : "success"}>{label}</Tag>
      {failed && syncing ? <Tag color="processing">正在重试同步</Tag> : null}
      {(failed || syncing) && source.stale ? <Typography.Text type="secondary">存档可能过期</Typography.Text> : null}
      <Typography.Text type="secondary" className={compact ? "hidden text-xs sm:inline" : undefined}>上次成功：{time(source.last_success)}</Typography.Text>
      <Tooltip title={compact ? `${buttonLabel} · 上次成功：${time(source.last_success)}` : buttonLabel}><Button type={compact ? "text" : "default"} size="small" aria-label={buttonLabel} icon={<ReloadOutlined />} loading={syncBusy} disabled={blocked || !snapshot.account.self_ali_id || snapshot.data_dir.state !== "ok"} onClick={() => void submit()}>{compact ? null : buttonLabel}</Button></Tooltip>
    </Space>
  );
}
