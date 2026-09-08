"use client";

import { Card, Space } from "antd";
import type { ReactNode } from "react";

type SessionListPanelProps = {
  title: ReactNode;
  loading?: boolean;
  children: ReactNode;
  minHeightClassName: string;
  extra?: ReactNode;
};

export function SessionListPanel({ title, loading, children, minHeightClassName, extra }: SessionListPanelProps) {
  const cardTitle = (
    <div className="flex items-center justify-between gap-2">
      <span>{title}</span>
      {extra ? <Space size="small">{extra}</Space> : null}
    </div>
  );

  return (
    <Card title={cardTitle} loading={loading} className={minHeightClassName}>
      {children}
    </Card>
  );
}
