"use client";

import { Button, Space } from "antd";
import Link from "next/link";
import { AccountSetup } from "@/features/settings/SettingsPage";

export default function Page() {
  return (
    <Space orientation="vertical" size="large" className="w-full">
      <AccountSetup />
        <Link href="/chat/customer-sessions">
          <Button type="primary">前往聊天工作台</Button>
        </Link>
    </Space>
  );
}
