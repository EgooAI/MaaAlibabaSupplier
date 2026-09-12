"use client";

import { Button, Empty } from "antd";
import Link from "next/link";

export default function Page() {
  return (
    <div className="flex min-h-[480px] items-center justify-center">
      <Empty description="首页暂无内容">
        <Link href="/chat/customer-sessions">
          <Button type="primary">前往聊天工作台</Button>
        </Link>
      </Empty>
    </div>
  );
}
