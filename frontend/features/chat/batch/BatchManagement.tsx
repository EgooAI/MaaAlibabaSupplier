"use client";

import { CheckOutlined, ClearOutlined, DownOutlined, ExportOutlined, MessageOutlined, SwapOutlined } from "@ant-design/icons";
import { Button, Card, Dropdown, Space, Tag, Typography } from "antd";
import { PORTRAIT_STAGE_OPTIONS, portraitTagLabel } from "@/domain/portrait/portraitModel";
import type { PortraitStage, PortraitTag } from "@/types/portrait";

type BatchManagementProps = {
  selectedCount: number;
  selectedCustomerCount: number;
  exporting: boolean;
  exportingPortraits: boolean;
  portraitBusy: boolean;
  portraitTags: PortraitTag[];
  onSelectAll: () => void;
  onInvert: () => void;
  onClear: () => void;
  onExport: () => void;
  onExportPortraits: () => void;
  onSetStage: (stage: PortraitStage) => void;
  onAddTag: (tagId: string) => void;
  onRemoveTag: (tagId: string) => void;
};

export function BatchManagement({
  selectedCount, selectedCustomerCount, exporting, exportingPortraits, portraitBusy, portraitTags,
  onSelectAll, onInvert, onClear, onExport, onExportPortraits, onSetStage, onAddTag, onRemoveTag,
}: BatchManagementProps) {
  const busy = portraitBusy || !selectedCount;
  const addableTags = portraitTags.filter((tag) => !tag.archived);
  return (
    <Card
      size="small"
      className="overflow-hidden border-slate-200"
      title={
        <div className="flex items-center gap-2">
          <Typography.Text strong>批量管理</Typography.Text>
          <Tag color={selectedCount ? "blue" : "default"}>{selectedCount} 个已选</Tag>
          {selectedCount ? <Tag color={selectedCustomerCount ? "geekblue" : "default"}>涉及 {selectedCustomerCount} 位客户</Tag> : null}
        </div>
      }
    >
      <div className="flex flex-col gap-4 rounded-lg bg-slate-50 px-4 py-3 md:flex-row md:items-center md:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600">
            <CheckOutlined />
          </div>
          <Typography.Text>仅操作当前页选中的会话；切换筛选或页码会清空选择。画像修改按客户生效，并影响该客户在当前工作区的所有会话。</Typography.Text>
        </div>
        <Space wrap size={[8, 8]} className="md:justify-end">
          <Button size="small" icon={<CheckOutlined />} onClick={onSelectAll}>全选当前页</Button>
          <Button size="small" icon={<SwapOutlined />} onClick={onInvert}>反选</Button>
          <Button size="small" icon={<ClearOutlined />} onClick={onClear}>清空</Button>
          <Dropdown
            disabled={busy}
            menu={{
              items: PORTRAIT_STAGE_OPTIONS.map((option) => ({ key: option.value, label: option.label })),
              onClick: ({ key }) => onSetStage(key as PortraitStage),
            }}
          >
            <Button size="small" loading={portraitBusy}>设置阶段 <DownOutlined /></Button>
          </Dropdown>
          <Dropdown
            disabled={busy || !addableTags.length}
            menu={{ items: addableTags.map((tag) => ({ key: tag.tag_id, label: tag.name })), onClick: ({ key }) => onAddTag(key) }}
          >
            <Button size="small" loading={portraitBusy}>增加标签 <DownOutlined /></Button>
          </Dropdown>
          <Dropdown
            disabled={busy || !portraitTags.length}
            menu={{
              items: portraitTags.map((tag) => ({ key: tag.tag_id, label: portraitTagLabel(tag) })),
              onClick: ({ key }) => onRemoveTag(key),
            }}
          >
            <Button size="small" loading={portraitBusy}>移除标签 <DownOutlined /></Button>
          </Dropdown>
          <Button size="small" icon={<ExportOutlined />} loading={exportingPortraits} disabled={exportingPortraits || !selectedCount} onClick={onExportPortraits}>导出客户资料</Button>
          <Button size="small" icon={<ExportOutlined />} loading={exporting} disabled={exporting || !selectedCount} onClick={onExport}>导出当前页所选聊天</Button>
          <Button size="small" icon={<MessageOutlined />} disabled>群发</Button>
        </Space>
      </div>
    </Card>
  );
}
