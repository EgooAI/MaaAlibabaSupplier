"use client";

import { App, Button, Space, Typography } from "antd";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { backend } from "@/services/client";
import type { DataDirStatus } from "@/types/status";

function bannerCopy(status: DataDirStatus): string {
  if (status.state === "unconfigured") return "尚未配置阿里客户端数据目录，聊天与同步功能不可用，请前往设置页配置。";
  return `数据目录无效：${status.detail || "请检查路径"}，聊天与同步功能不可用，请前往设置页重新配置。`;
}

export function DataDirBanner() {
  const router = useRouter();
  const { message } = App.useApp();
  const [copy, setCopy] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function checkBlockingState() {
      try {
        const status = await backend.getDataDirStatus();
        if (cancelled) return;
        if (status.state !== "ok") {
          setCopy(bannerCopy(status));
          return;
        }
        const identities = await backend.listAliIds();
        if (cancelled) return;
        if (!identities.selected) setCopy("尚未选择阿里账号身份，聊天与同步功能不可用，请前往设置页选择。");
      } catch {
        if (!cancelled) message.error("运行状态加载失败");
      }
    }

    void checkBlockingState();
    return () => {
      cancelled = true;
    };
  }, [message]);

  if (!copy) return null;

  return (
    <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
      <Space className="w-full justify-between">
        <Typography.Text>{copy}</Typography.Text>
        <Button type="primary" size="small" onClick={() => router.push("/settings")}>
          前往设置
        </Button>
      </Space>
    </div>
  );
}
