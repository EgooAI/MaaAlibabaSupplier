"use client";

import { Card, Space, Typography } from "antd";
import { BatchManagement } from "./batch/BatchManagement";
import { ConversationList } from "./conversation/ConversationList";
import { useBatchManagement } from "./hooks/useBatchManagement";

export function BatchPage() {
  const workbench = useBatchManagement();

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <Typography.Title level={2} className="!mb-1">批量管理</Typography.Title>
      <BatchManagement
        selectedCount={workbench.selectedCount}
        exporting={workbench.exporting}
        onSelectAll={workbench.selectAll}
        onInvert={workbench.invertSelection}
        onClear={workbench.clearSelection}
        onExport={workbench.exportSelected}
      />
      <Card title="选择会话" loading={workbench.loading} className="min-h-[720px]">
        <ConversationList
          conversations={workbench.conversations}
          selectedIds={workbench.selectedIds}
          groupMode={workbench.groupMode}
          onGroupModeChange={workbench.setGroupMode}
          onToggleSelected={workbench.toggleSelected}
          onSelectGroup={workbench.setGroupSelected}
          selectable
        />
      </Card>
    </Space>
  );
}
