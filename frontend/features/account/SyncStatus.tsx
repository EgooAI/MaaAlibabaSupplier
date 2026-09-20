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
  const verifying = source.key_validation === "verifying";
  const retrying = failed && source.retry_at != null && (source.key_validation === "unverified" || source.key_validation === "valid");
  const syncing = source.syncing || (!retrying && (source.pending || source.phase === "syncing"));
  const active = verifying || syncing;
  const keyBlocked = source.key_validation === "invalid" || source.key_validation === "unavailable";
  const label = blocked ? "连接中断，账号状态待刷新" : refreshError ? (refreshPending ? "聊天读取失败，正在重试" : "聊天读取失败，等待重试") : refreshPending ? "正在刷新聊天" : verifying ? "密钥验证中" : syncing ? "同步中" : retrying ? "暂时不可用，等待自动重试" : keyBlocked ? "密钥不可用，请在设置中处理" : failed ? "同步失败" : source.key_validation === "unverified" ? "密钥待验证" : source.observation_stale ? "源库观察已过期，存档可能过期" : !source.ready ? "等待聊天同步" : source.freshness === "stale" ? "存档可能过期" : "聊天已更新";
  const actionLabel = verifying ? "密钥验证中" : syncing ? "同步中" : retrying ? "等待自动重试" : buttonLabel;
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
      <Tag color={blocked || refreshError ? "error" : active || refreshPending ? "processing" : retrying ? "warning" : failed || keyBlocked ? "error" : !source.ready || source.freshness === "stale" ? "warning" : "success"}>{label}</Tag>
      {failed && syncing ? <Tag color="processing">正在重试同步</Tag> : null}
      {retrying && !active ? <Typography.Text type="secondary">自动重试时间：{time(source.retry_at)}</Typography.Text> : null}
      {(failed || active) && source.stale ? <Typography.Text type="secondary">存档可能过期</Typography.Text> : null}
      <Typography.Text type="secondary" className={compact ? "hidden text-xs sm:inline" : undefined}>上次成功：{time(source.last_success)}</Typography.Text>
      <Tooltip title={compact ? `${actionLabel} · 上次成功：${time(source.last_success)}` : actionLabel}><Button type={compact ? "text" : "default"} size="small" aria-label={actionLabel} icon={<ReloadOutlined />} loading={syncBusy || active} disabled={blocked || active || retrying || !snapshot.account.self_ali_id || snapshot.data_dir.state !== "ok"} onClick={() => void submit()}>{compact ? null : actionLabel}</Button></Tooltip>
    </Space>
  );
}
