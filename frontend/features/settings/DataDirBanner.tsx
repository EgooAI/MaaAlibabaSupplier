"use client";

import { Alert, Tooltip } from "antd";
import Link from "next/link";
import { useAccount } from "@/features/account/AccountProvider";

// Margin-free by design: the surrounding layout owns all spacing, so the same
// banner collapses cleanly in cards, Space stacks and bare page fallbacks.
export function DataDirBanner({ compact = false }: { compact?: boolean }) {
  const { snapshot, blocked, error } = useAccount();
  if (!blocked && snapshot?.capabilities.operate_client && snapshot.capabilities.read_chat) return null;
  if (error) {
    return <Alert type="error" showIcon title="账号状态不可用" description={error} action={<Link href="/settings">前往接入设置</Link>} />;
  }
  if (blocked || !snapshot) {
    return <Alert type="info" showIcon title="正在刷新账号状态" description="请稍候，刷新完成后此提示会自动消失。" />;
  }
  if (!snapshot.capabilities.read_chat) {
    const source = snapshot.source;
    const retrying = Boolean(source.last_error && source.retry_at != null && (source.key_validation === "unverified" || source.key_validation === "valid"));
    const active = source.key_validation === "verifying" || source.syncing || (!retrying && source.pending);
    return (
      <Alert
        type={active ? "info" : "warning"}
        showIcon
        title="聊天数据尚未就绪"
        description={active ? "正在自动验证密钥或同步聊天，请等待完成，无需重复点击。" : retrying ? "数据源暂时不可用，后台将自动重试，无需重复点击。" : "请在设置中检查数据目录、卖家账号与密钥，并验证接入。"}
        action={<Link href="/settings">前往接入设置</Link>}
      />
    );
  }
  if (!snapshot.client.connected) {
    const description = "当前可以阅读聊天、编辑草稿；发送、填入测试和跳转联系人需要先在设置中接入客户端。";
    return (
      <Alert
        type="warning"
        showIcon
        title={compact ? <Tooltip title={description}>只读模式：客户端尚未接入</Tooltip> : "只读模式：客户端尚未接入"}
        description={compact ? undefined : description}
        action={<Link href="/settings">前往接入设置</Link>}
      />
    );
  }
  return (
    <Alert
      type="warning"
      showIcon
      title="客户端暂不可操作"
      description="请在设置中选择卖家账号并检查客户端连接。"
      action={<Link href="/settings">前往接入设置</Link>}
    />
  );
}
