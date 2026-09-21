"use client";

import { Button, Card, Descriptions, Empty, Space, Spin, Tag } from "antd";
import { BaseDrawer } from "@/components/BaseDrawer";
import { getDisplayName } from "@/domain/user";
import { EMPTY_TEXT } from "@/components/empty";
import type { SelfInfo } from "@/types/home";

type ProfileDrawerProps = {
  selfInfo: SelfInfo | null | undefined;
  loading: boolean;
  error?: string;
  open: boolean;
  onClose: () => void;
  onRetry: () => void;
};

export function ProfileDrawer({ selfInfo, loading, error, open, onClose, onRetry }: ProfileDrawerProps) {
  return (
    <BaseDrawer title="个人信息" open={open} onClose={onClose}>
      {loading ? <div className="flex min-h-40 items-center justify-center"><Spin /></div> : null}
      {!loading && error ? <Empty description={error}><Button onClick={onRetry}>重试</Button></Empty> : null}
      {!loading && !error && !selfInfo ? <Empty description={EMPTY_TEXT.profile}><Button onClick={onRetry}>重试</Button></Empty> : null}
      {!loading && !error && selfInfo ? (
        <Space orientation="vertical" className="w-full" size="middle">
          <Card>
            <Descriptions size="small" column={1} items={[
              { key: "name", label: "姓名", children: getDisplayName(selfInfo) },
              { key: "loginId", label: "登录账号", children: selfInfo.login_id },
              { key: "aliId", label: "账号 ID", children: selfInfo.ali_id },
              { key: "company", label: "公司", children: selfInfo.company_name },
              { key: "country", label: "国家", children: selfInfo.country },
              { key: "status", label: "账号状态", children: selfInfo.account_status },
            ]} />
            <div className="mt-3">
              <Tag color={selfInfo.account_status === "active" ? "green" : "default"}>{selfInfo.account_status}</Tag>
            </div>
          </Card>
        </Space>
      ) : null}
    </BaseDrawer>
  );
}
