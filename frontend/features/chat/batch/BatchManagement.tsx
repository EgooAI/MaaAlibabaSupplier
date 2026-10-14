"use client";

import { CheckOutlined, ClearOutlined, SwapOutlined } from "@ant-design/icons";
import { Button, Card, Space, Tag, Typography } from "antd";

type BatchManagementProps = {
  selectedCount: number;
  selectedCustomerCount: number;
  onSelectAll: () => void;
  onInvert: () => void;
  onClear: () => void;
};

export function BatchManagement({ selectedCount, selectedCustomerCount, onSelectAll, onInvert, onClear }: BatchManagementProps) {
  return (
    <Card
      size="small"
      className="border-slate-200"
      title={
        <div className="flex items-center gap-2">
          <Typography.Text strong>批量管理</Typography.Text>
          <Tag color={selectedCount ? "blue" : "default"}>{selectedCount} 个会话</Tag>
          {selectedCount ? <Tag color={selectedCustomerCount ? "geekblue" : "default"}>涉及 {selectedCustomerCount} 位客户</Tag> : null}
        </div>
      }
    >
      <Space wrap size={8}>
        <Button size="small" icon={<CheckOutlined />} onClick={onSelectAll}>全选当前页</Button>
        <Button size="small" icon={<SwapOutlined />} onClick={onInvert}>反选</Button>
        <Button size="small" icon={<ClearOutlined />} onClick={onClear}>清空</Button>
      </Space>
    </Card>
  );
}
