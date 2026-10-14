"use client";

import { Card, Space, Spin } from "antd";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { inboxQueryFromUrl } from "@/domain/chat/inboxModel";
import { groupCustomers } from "@/domain/portrait/customerGroups";
import { ConversationFilters, ConversationPaging } from "./conversation/ConversationFilters";
import { BatchActionPanel } from "./batch/BatchActionPanel";
import { BatchFloatingBar } from "./batch/BatchFloatingBar";
import { BatchManagement } from "./batch/BatchManagement";
import { BatchCustomerDrawer } from "./batch/BatchCustomerDrawer";
import { BatchCustomerGrid } from "./batch/BatchCustomerGrid";
import { useBatchManagement } from "./hooks/useBatchManagement";
import { useAccount } from "@/features/account/AccountProvider";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { SyncStatus } from "@/features/account/SyncStatus";

export function BatchPage() {
  const { snapshot, blocked, suspended, generation } = useAccount();
  if ((blocked && !suspended) || !snapshot?.capabilities.read_chat) return <DataDirBanner />;
  return <Suspense fallback={<Spin />}><BatchWorkspace key={`${snapshot.account.epoch}:${generation}`} /></Suspense>;
}

function BatchWorkspace() {
  const params = useSearchParams().toString();
  const workbench = useBatchManagement(inboxQueryFromUrl(new URLSearchParams(params)));
  const { changeQuery } = workbench;
  const previousParams = useRef(params);
  useEffect(() => {
    if (previousParams.current === params) return;
    previousParams.current = params;
    changeQuery(inboxQueryFromUrl(new URLSearchParams(params)));
  }, [params, changeQuery]);
  const [drawerKey, setDrawerKey] = useState<string | null>(null);
  const drawerGroup = useMemo(
    () => (drawerKey ? groupCustomers(workbench.conversations).find((group) => group.key === drawerKey) ?? null : null),
    [drawerKey, workbench.conversations],
  );
  const panelTargets = useMemo(
    () => (workbench.portraitPanel ? workbench.conversations.filter((conversation) => workbench.portraitPanel!.sessionIds.includes(conversation.id)) : []),
    [workbench.portraitPanel, workbench.conversations],
  );
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (drawerKey) setDrawerKey(null);
      else if (workbench.portraitPanel) workbench.closePortraitPanel();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerKey, workbench]);

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <DataDirBanner />
      <SyncStatus refreshError={workbench.refreshError} refreshPending={workbench.refreshPending} />
      <BatchManagement
        selectedCount={workbench.selectedCount}
        selectedCustomerCount={workbench.selectedCustomerCount}
        exporting={workbench.exporting}
        exportingPortraits={workbench.exportingPortraits}
        portraitBusy={workbench.portraitBusy}
        activeAction={workbench.portraitPanel?.action ?? null}
        onSelectAll={workbench.selectAll}
        onInvert={workbench.invertSelection}
        onClear={workbench.clearSelection}
        onExport={workbench.exportSelected}
        onExportPortraits={() => void workbench.exportPortraits()}
        onOpenPanel={(action) => workbench.openPortraitPanel(action)}
      />
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start">
        <Card title="选择会话" className="min-w-0 flex-1">
          <ConversationFilters key={params} query={inboxQueryFromUrl(new URLSearchParams(params))} onChange={workbench.changeQuery} tagOptions={workbench.portraitTags} />
          <ConversationPaging page={workbench.page} pending={workbench.pagePending} onChange={workbench.changePage} />
          <BatchCustomerGrid
            conversations={workbench.conversations}
            selectedIds={workbench.selectedIds}
            loading={workbench.loading}
            onToggleSessions={workbench.setGroupSelected}
            onToggleAll={(selected) => (selected ? workbench.selectAll() : workbench.clearSelection())}
            onOpenCustomer={setDrawerKey}
            onOpenStage={(sessionIds) => workbench.openPortraitPanel("stage", sessionIds)}
            onOpenAddTag={(sessionIds) => workbench.openPortraitPanel("add-tag", sessionIds)}
          />
        </Card>
        {workbench.portraitPanel ? (
          <div className="xl:sticky xl:top-4 xl:w-[380px] xl:shrink-0">
            <BatchActionPanel
              key={`${workbench.portraitPanel.action}:${workbench.portraitPanel.sessionIds.join(",")}`}
              action={workbench.portraitPanel.action}
              targets={panelTargets}
              portraitTags={workbench.portraitTags}
              busy={workbench.portraitBusy}
              onActionChange={workbench.changePanelAction}
              onClose={workbench.closePortraitPanel}
              onApply={(changes, description) => void workbench.applyPortraitChanges(changes, description)}
            />
          </div>
        ) : null}
      </div>
      <BatchCustomerDrawer group={drawerGroup} open={Boolean(drawerGroup)} onClose={() => setDrawerKey(null)} portraitTags={workbench.portraitTags} />
      <BatchFloatingBar
        selectedCount={workbench.selectedCount}
        selectedCustomerCount={workbench.selectedCustomerCount}
        busy={workbench.portraitBusy}
        exporting={workbench.exporting}
        exportingPortraits={workbench.exportingPortraits}
        onOpenPanel={(action) => workbench.openPortraitPanel(action)}
        onExportPortraits={() => void workbench.exportPortraits()}
        onExport={workbench.exportSelected}
        onClear={workbench.clearSelection}
      />
      {workbench.selectedCount ? <div className="h-12" aria-hidden /> : null}
    </Space>
  );
}
