"use client";

import { Card, Space } from "antd";
import { BatchManagement } from "./batch/BatchManagement";
import { ConversationList } from "./conversation/ConversationList";
import { useBatchManagement } from "./hooks/useBatchManagement";
import { useAccount } from "@/features/account/AccountProvider";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { SyncStatus } from "@/features/account/SyncStatus";

export function BatchPage() {
  const { snapshot, blocked, suspended, generation } = useAccount();
  if ((blocked && !suspended) || !snapshot?.capabilities.read_chat) return <DataDirBanner />;
  return <BatchWorkspace key={`${snapshot.account.epoch}:${generation}`} />;
}

function BatchWorkspace() {
  const workbench = useBatchManagement();

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <DataDirBanner />
      <SyncStatus refreshError={workbench.refreshError} refreshPending={workbench.refreshPending} />
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
