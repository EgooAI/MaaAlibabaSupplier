"use client";

import { Card, Space } from "antd";
import type { ReactNode } from "react";

type SessionListPanelProps = {
  title: ReactNode;
  loading?: boolean;
  children: ReactNode;
  extra?: ReactNode;
};

export function SessionListPanel({ title, loading, children, extra }: SessionListPanelProps) {
  const cardTitle = (
    <div className="flex items-center justify-between gap-2">
      <span>{title}</span>
      {extra ? <Space size="small">{extra}</Space> : null}
    </div>
  );

  return (
    <Card
      title={cardTitle}
      loading={loading}
      className="flex h-full min-h-0 w-full flex-col"
      classNames={{ body: "flex min-h-0 flex-1 flex-col overflow-y-auto" }}
    >
      {children}
    </Card>
  );
}
