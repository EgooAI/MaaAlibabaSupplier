"use client";

import { Alert } from "antd";
import Link from "next/link";
import { useAccount } from "@/features/account/AccountProvider";

export function DataDirBanner() {
  const { snapshot, blocked, error } = useAccount();
  if (!blocked && snapshot?.capabilities.operate_client) return null;
  const copy = error || (blocked ? "正在刷新账号状态，请稍候。" : !snapshot?.capabilities.read_chat ? "聊天数据尚未就绪，请完成目录、账号和 Key 配置，然后主动同步。" : !snapshot.client.confirmed ? "只读模式：卖家身份尚未人工确认。草稿可编辑，发送、填入测试和跳转联系人暂不可用。" : "客户端暂不可操作，请检查连接并重新确认卖家身份。");
  return <Alert className="mb-4" type="warning" showIcon title={copy} action={<Link href="/settings">前往接入设置</Link>} />;
}
