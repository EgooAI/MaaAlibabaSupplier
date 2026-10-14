"use client";

import { CheckOutlined, ClearOutlined, ExportOutlined, MessageOutlined, SwapOutlined } from "@ant-design/icons";
import { Button, Card, Space, Tag, Typography } from "antd";
import type { BatchPanelAction } from "@/domain/portrait/batchPreview";

type BatchManagementProps = {
  selectedCount: number;
  selectedCustomerCount: number;
  exporting: boolean;
  exportingPortraits: boolean;
  portraitBusy: boolean;
  activeAction?: BatchPanelAction | null;
  onSelectAll: () => void;
  onInvert: () => void;
  onClear: () => void;
  onExport: () => void;
  onExportPortraits: () => void;
  onOpenPanel: (action: BatchPanelAction) => void;
};

export function BatchManagement({
  selectedCount, selectedCustomerCount, exporting, exportingPortraits, portraitBusy, activeAction,
  onSelectAll, onInvert, onClear, onExport, onExportPortraits, onOpenPanel,
}: BatchManagementProps) {
  const busy = portraitBusy || !selectedCount;
  return (
    <Card
      size="small"
      className="overflow-hidden border-slate-200"
      title={
        <div className="flex items-center gap-2">
          <Typography.Text strong>批量管理</Typography.Text>
          <Tag color={selectedCount ? "blue" : "default"}>{selectedCount} 个会话</Tag>
          {selectedCount ? <Tag color={selectedCustomerCount ? "geekblue" : "default"}>涉及 {selectedCustomerCount} 位客户</Tag> : null}
        </div>
      }
    >
      <div className="flex flex-col gap-4 rounded-lg bg-slate-50 px-4 py-3 md:flex-row md:items-center md:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600">
            <CheckOutlined />
          </div>
          <Typography.Text>仅操作当前页选中的会话；画像按客户生效。</Typography.Text>
        </div>
        <Space wrap size={[8, 8]} className="md:justify-end">
          <Button size="small" icon={<CheckOutlined />} onClick={onSelectAll}>全选当前页</Button>
          <Button size="small" icon={<SwapOutlined />} onClick={onInvert}>反选</Button>
          <Button size="small" icon={<ClearOutlined />} onClick={onClear}>清空</Button>
          <Button size="small" type={activeAction === "stage" ? "primary" : "default"} disabled={busy} loading={portraitBusy} onClick={() => onOpenPanel("stage")}>设置阶段</Button>
          <Button size="small" type={activeAction === "add-tag" ? "primary" : "default"} disabled={busy} loading={portraitBusy} onClick={() => onOpenPanel("add-tag")}>增加标签</Button>
          <Button size="small" type={activeAction === "remove-tag" ? "primary" : "default"} disabled={busy} loading={portraitBusy} onClick={() => onOpenPanel("remove-tag")}>移除标签</Button>
          <Button size="small" icon={<ExportOutlined />} loading={exportingPortraits} disabled={exportingPortraits || !selectedCount} onClick={onExportPortraits}>导出客户资料</Button>
          <Button size="small" icon={<ExportOutlined />} loading={exporting} disabled={exporting || !selectedCount} onClick={onExport}>导出当前页所选聊天</Button>
          <Button size="small" icon={<MessageOutlined />} disabled>群发</Button>
        </Space>
      </div>
    </Card>
  );
}
