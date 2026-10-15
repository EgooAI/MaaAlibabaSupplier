"use client";

import { Card, Space, Spin } from "antd";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { inboxQueryFromUrl, inboxQueryToUrl } from "@/domain/chat/inboxModel";
import { groupCustomers } from "@/domain/portrait/customerGroups";
import type { ConversationQuery } from "@/types/inbox";
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
  const router = useRouter();
  const pathname = usePathname();
  const workbench = useBatchManagement(inboxQueryFromUrl(new URLSearchParams(params)));
  const { changeQuery } = workbench;
  // 已应用筛选写回 URL（未应用即为空查询），刷新与直达链接保留筛选。
  const writeQueryToUrl = (query: ConversationQuery) => {
    void router.replace(inboxQueryToUrl(query, null, pathname), { scroll: false });
  };
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
  const panel = workbench.panel;
  const panelTargets = useMemo(
    () => (panel ? workbench.conversations.filter((conversation) => panel.sessionIds.includes(conversation.id)) : []),
    [panel, workbench.conversations],
  );
  const panelRef = useRef<HTMLDivElement>(null);
  const panelWasOpen = useRef(false);
  // 面板堆叠到网格下方（< xl）时滚动到面板，避免行内入口打开后“看不到反应”。
  useEffect(() => {
    const open = Boolean(panel);
    const wasOpen = panelWasOpen.current;
    panelWasOpen.current = open;
    if (!open || wasOpen) return;
    if (window.matchMedia("(min-width: 1280px)").matches) return;
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [panel]);
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (drawerKey) setDrawerKey(null);
      else if (panel) workbench.closePanel();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerKey, panel, workbench]);

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <DataDirBanner />
      <SyncStatus refreshError={workbench.refreshError} refreshPending={workbench.refreshPending} />
      <BatchManagement
        selectedCount={workbench.selectedCount}
        selectedCustomerCount={workbench.selectedCustomerCount}
        onSelectAll={workbench.selectAll}
        onInvert={workbench.invertSelection}
        onClear={workbench.clearSelection}
      />
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start">
        <Card title="选择会话" className="min-w-0 flex-1">
          <ConversationFilters key={params} query={inboxQueryFromUrl(new URLSearchParams(params))} onChange={workbench.changeQuery} tagOptions={workbench.portraitTags} onApplied={writeQueryToUrl} />
          <ConversationPaging page={workbench.page} pending={workbench.pagePending} onChange={workbench.changePage} />
          <BatchCustomerGrid
            conversations={workbench.conversations}
            selectedIds={workbench.selectedIds}
            loading={workbench.loading}
            onToggleSessions={workbench.setGroupSelected}
            onToggleAll={(selected) => (selected ? workbench.selectAll() : workbench.clearSelection())}
            onOpenCustomer={setDrawerKey}
            onOpenStage={(sessionIds) => workbench.openPanel("stage", sessionIds)}
            onOpenAddTag={(sessionIds) => workbench.openPanel("add-tag", sessionIds)}
          />
        </Card>
        {panel ? (
          <div ref={panelRef} className="xl:sticky xl:top-4 xl:w-[380px] xl:shrink-0">
            <BatchActionPanel
              key={panel.sessionIds.join(",")}
              action={panel.action}
              targets={panelTargets}
              portraitTags={workbench.portraitTags}
              busy={workbench.portraitBusy}
              exporting={workbench.exporting}
              exportingPortraits={workbench.exportingPortraits}
              onActionChange={workbench.changePanelAction}
              onClose={workbench.closePanel}
              onApply={(changes, description) => void workbench.applyPortraitChanges(changes, description)}
              onExport={(kind) => void (kind === "portrait" ? workbench.exportPortraits() : workbench.exportSelected()).then(() => workbench.closePanel())}
            />
          </div>
        ) : null}
      </div>
      <BatchCustomerDrawer group={drawerGroup} open={Boolean(drawerGroup)} onClose={() => setDrawerKey(null)} portraitTags={workbench.portraitTags} onPortraitTagsChanged={() => void workbench.reloadPortraitTags()} />
      <BatchFloatingBar
        selectedCount={workbench.selectedCount}
        selectedCustomerCount={workbench.selectedCustomerCount}
        busy={workbench.portraitBusy}
        activeAction={panel?.action ?? null}
        shifted={Boolean(panel)}
        onOpenPanel={workbench.openPanel}
        onClear={workbench.clearSelection}
      />
      {workbench.selectedCount ? <div className="h-16" aria-hidden /> : null}
    </Space>
  );
}
