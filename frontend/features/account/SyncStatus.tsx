"use client";

import { ReloadOutlined } from "@ant-design/icons";
import { App, Button, Descriptions, Space, Tag, Typography } from "antd";
import { AccountChangedError, useAccount } from "./AccountProvider";

function time(value: number | null) {
  return value == null ? "尚无记录" : new Date(value * 1000).toLocaleString();
}

export function SyncStatus({ details = false, refreshError = false, refreshPending = false, buttonLabel = "刷新聊天" }: { details?: boolean; refreshError?: boolean; refreshPending?: boolean; buttonLabel?: string }) {
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
    <Space orientation="vertical" size={4} className="w-full" role="status">
      <Space wrap size="small">
        <Tag color={blocked || failed || refreshError ? "error" : syncing || refreshPending ? "processing" : source.freshness === "stale" ? "warning" : "success"}>{label}</Tag>
        {failed && syncing ? <Tag color="processing">正在重试同步</Tag> : null}
        {(failed || syncing) && source.stale ? <Typography.Text type="secondary">存档可能过期</Typography.Text> : null}
        <Typography.Text type="secondary">上次成功：{time(source.last_success)}</Typography.Text>
        <Button size="small" icon={<ReloadOutlined />} loading={syncBusy} disabled={blocked || !snapshot.account.self_ali_id || snapshot.data_dir.state !== "ok"} onClick={() => void submit()}>{buttonLabel}</Button>
      </Space>
      {details ? <Descriptions size="small" column={1} items={[
        { key: "checked", label: "最近检查", children: time(source.last_checked) },
        { key: "attempt", label: "最近尝试", children: time(source.last_attempt) },
        { key: "retry", label: "计划重试", children: source.retry_at == null ? "无" : time(source.retry_at) },
        { key: "counts", label: "最近提交", children: `新增 ${source.counts.inserted} / 更新 ${source.counts.updated} / 未变 ${source.counts.unchanged}` },
        { key: "versions", label: "同步版本", children: `已提交 ${source.revision} / 数据源 ${source.source_revision} / 已应用 ${source.applied_source_revision}` },
        { key: "auto", label: "后台检查", children: `${source.auto_enabled ? "已启用" : "未启用"}${source.source_dirty ? " / 数据源有变化" : ""}${source.pending ? " / 等待同步" : ""}` },
        ...(source.last_error ? [{ key: "error", label: "同步错误", children: source.last_error }] : []),
      ]} /> : null}
    </Space>
  );
}
