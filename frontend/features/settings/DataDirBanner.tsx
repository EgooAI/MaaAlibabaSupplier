"use client";

import { Alert, Tooltip } from "antd";
import Link from "next/link";
import { useAccount } from "@/features/account/AccountProvider";

// Margin-free by design: the surrounding layout owns all spacing, so the same
// banner collapses cleanly in cards, Space stacks and bare page fallbacks.
export function DataDirBanner({ compact = false }: { compact?: boolean }) {
  const { snapshot, blocked, error } = useAccount();
  if (!blocked && snapshot?.capabilities.operate_client) return null;
  if (error) {
    return <Alert type="error" showIcon title="账号状态不可用" description={error} action={<Link href="/settings">前往接入设置</Link>} />;
  }
  if (blocked || !snapshot) {
    return <Alert type="info" showIcon title="正在刷新账号状态" description="请稍候，刷新完成后此提示会自动消失。" />;
  }
  if (!snapshot.capabilities.read_chat) {
    return (
      <Alert
        type="warning"
        showIcon
        title="聊天数据尚未就绪"
        description="请完成数据目录、卖家账号与密钥配置，然后主动验证并同步。"
        action={<Link href="/settings">前往接入设置</Link>}
      />
    );
  }
  if (!snapshot.client.confirmed) {
    const description = "当前可以阅读聊天、编辑草稿；发送、填入测试和跳转联系人不可用。在设置中核对并确认卖家后解除。";
    return (
      <Alert
        type="warning"
        showIcon
        title={compact ? <Tooltip title={description}>只读模式：卖家身份尚未人工确认</Tooltip> : "只读模式：卖家身份尚未人工确认"}
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
      description="请检查客户端连接，并重新确认卖家身份。"
      action={<Link href="/settings">前往接入设置</Link>}
    />
  );
}
