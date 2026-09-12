"use client";

import { Card, Space } from "antd";
import { BatchManagement } from "./batch/BatchManagement";
import { ConversationList } from "./conversation/ConversationList";
import { useBatchManagement } from "./hooks/useBatchManagement";

export function BatchPage() {
  const workbench = useBatchManagement();

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <BatchManagement
        selectedCount={workbench.selectedCount}
        exporting={workbench.exporting}
        onSelectAll={workbench.selectAll}
        onInvert={workbench.invertSelection}
        onClear={workbench.clearSelection}
        onExport={workbench.exportSelected}
      />
      <Card title="选择会话" loading={workbench.loading}>
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
