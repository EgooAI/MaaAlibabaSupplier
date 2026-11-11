"use client";

import { ArrowLeftOutlined, CheckOutlined, SettingOutlined, UserOutlined } from "@ant-design/icons";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button, Card, Empty, Popover, Spin, Tooltip } from "antd";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { inboxQueryFromUrl, inboxQueryToUrl } from "@/domain/chat/inboxModel";
import { statusLabel, waitingLabel } from "@/domain/chat/chatModel";
import { countryCodeOf } from "@/domain/country";
import { ConversationFilters, ConversationPaging } from "./conversation/ConversationFilters";
import { InboxBadges } from "./conversation/InboxBadges";
import { CardDetailDrawer } from "@/components/CardDetailDrawer";
import { CountryFlag } from "@/components/CountryFlag";
import { SessionListPanel } from "@/components/SessionListPanel";
import { useSplitSessionMobile } from "@/components/SplitSessionLayout";
import { ConversationList } from "./conversation/ConversationList";
import { CustomerInfo } from "./conversation/CustomerInfo";
import { useChatWorkbench } from "./hooks/useChatWorkbench";
import { useLocalTime } from "./hooks/useLocalTime";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { ForkSessionDrawer } from "./modals/ForkSessionDrawer";
import { ChatAnalysisModal } from "./modals/ChatAnalysisModal";
import { ChatConversation } from "./ChatConversation";
import { useAccount } from "@/features/account/AccountProvider";
import { SyncStatus } from "@/features/account/SyncStatus";
import type { ConversationQuery } from "@/types/inbox";

type AnalysisFocus = "intent" | "stage";

export function ChatPage() {
  const { snapshot, blocked, suspended, generation } = useAccount();
  if ((blocked && !suspended) || !snapshot?.capabilities.read_chat) return <DataDirBanner />;
  return <Suspense fallback={<Spin />}><ChatWorkspace key={`${snapshot.account.epoch}:${generation}`} /></Suspense>;
}

function ChatWorkspace() {
  const { blocked } = useAccount();
  const router = useRouter();
  const { isMobile, mobileView, setMobileView } = useSplitSessionMobile();
  const params = useSearchParams().toString();
  const pathname = usePathname();
  const workbench = useChatWorkbench(inboxQueryFromUrl(new URLSearchParams(params)), new URLSearchParams(params).get("conversation") ?? undefined);
  const { changeQuery } = workbench;
  // 已应用筛选写回 URL（不含分页令牌），会话深链保持不变；刷新后不丢失筛选。
  const writeQueryToUrl = (query: ConversationQuery) => {
    router.replace(inboxQueryToUrl(query, new URLSearchParams(params).get("conversation"), pathname), { scroll: false });
  };
  const previousParams = useRef(params);
  useEffect(() => {
    if (previousParams.current === params) return;
    previousParams.current = params;
    changeQuery(inboxQueryFromUrl(new URLSearchParams(params)));
  }, [params, changeQuery]);
  const active = workbench.activeConversation;
  const localTime = useLocalTime(active?.customer.country);
  const activeCountryCode = countryCodeOf(active?.customer.country);
  const headerMeta = [localTime, active?.customer.company].filter(Boolean).join(" · ");
  const [customerInfoOpen, setCustomerInfoOpen] = useState(false);
  const [forkIndex, setForkIndex] = useState<number>();
  const [analysisFocus, setAnalysisFocus] = useState<AnalysisFocus>("intent");

  function handleSelectConversation(id: string) {
    if (isMobile) setMobileView("detail");
    void workbench.selectConversation(id);
  }

  function openAnalysis(focus: AnalysisFocus) {
    setAnalysisFocus(focus);
    workbench.setAnalysisOpen(true);
    void workbench.analyzeConversation();
  }

  const sessionList = (
    <SessionListPanel
      title="会话列表"
      loading={workbench.loading}
      extra={
        <Tooltip title="管理会话">
          <Button
            type="text"
            size="small"
            aria-label="管理会话"
            icon={<SettingOutlined />}
            onClick={() => {
              // 跳转携带当前筛选（会话深链不属于批量页）。
              const search = new URLSearchParams(params);
              search.delete("conversation");
              const queries = search.toString();
              router.push(queries ? `/chat/batch?${queries}` : "/chat/batch");
            }}
          />
        </Tooltip>
      }
    >
      <ConversationFilters key={params} query={inboxQueryFromUrl(new URLSearchParams(params))} onChange={workbench.changeQuery} tagOptions={workbench.portraitTags} onApplied={writeQueryToUrl} />
      <div className="app-scroll min-h-0 flex-1 overflow-y-auto">
        <ConversationList
          conversations={workbench.conversations}
          activeId={active?.id}
          groupMode={workbench.groupMode}
          onGroupModeChange={workbench.setGroupMode}
          onSelect={handleSelectConversation}
        />
      </div>
      <ConversationPaging page={workbench.page} pending={workbench.pagePending} onChange={workbench.changePage} />
    </SessionListPanel>
  );
  const sessionDetail = (
    <Card
      title={<div className="flex min-w-0 items-center gap-2 py-3">
        {isMobile ? <Button type="text" aria-label="返回列表" icon={<ArrowLeftOutlined />} onClick={() => setMobileView("list")} /> : null}
        <div className="min-w-0">
          <div className="truncate">{active?.customer.name || "消息时间线"}</div>
          {active && (headerMeta || activeCountryCode) ? (
            <div className="truncate text-xs font-normal text-slate-500">
              <CountryFlag country={active.customer.country} showName />
              {headerMeta ? <span>{activeCountryCode ? " · " : ""}{headerMeta}</span> : null}
            </div>
          ) : null}
        </div>
      </div>}
      extra={
        <div className="flex items-center gap-1">
          {active ? <Popover title="会话状态" trigger="click" placement="bottomRight" content={
            <div className="flex max-w-64 flex-col gap-3">
              <InboxBadges conversation={active} />
              {active.unreadCount > 0 ? <Button size="small" icon={<CheckOutlined />} disabled={blocked || !active.readSnapshot} loading={workbench.markingRead} onClick={() => void workbench.markRead()}>标记工作台已读</Button> : null}
            </div>
          }>
            <Button type="text" size="small" aria-label="查看会话状态" className="!text-xs">
              <span className={active.replyState === "needs_reply" || active.uncertain ? "text-amber-700" : "text-slate-500"}>{active.uncertain ? "待核实" : waitingLabel(active) ?? statusLabel(active.replyState)}</span>
              {active.unreadCount > 0 ? <span className="h-1.5 w-1.5 rounded-full bg-blue-500" aria-label={`本工作台未读 ${active.unreadCount}`} /> : null}
            </Button>
          </Popover> : null}
          <Button type="text" icon={<UserOutlined />} aria-label="客户信息" onClick={() => setCustomerInfoOpen(true)} disabled={!active}><span className="hidden sm:inline">客户信息</span></Button>
        </div>
      }
      className="flex h-full min-h-0 w-full flex-col"
      styles={{ header: { padding: "0 16px", flexShrink: 0 }, body: { padding: 0 } }}
      classNames={{ body: "flex min-h-0 flex-1 flex-col overflow-hidden" }}
    >
      {active ? (
        <ChatConversation key={active.id} active={active} workbench={workbench} onFork={setForkIndex} onAnalysis={openAnalysis} />
      ) : (
        <div className="flex flex-1 items-center justify-center"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择一个会话，开始沟通" /></div>
      )}
    </Card>
  );

  return (
    <>
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="max-h-[25%] shrink-0 overflow-y-auto">
          <DataDirBanner compact />
          <div className="mt-2"><SyncStatus compact refreshError={workbench.refreshError} refreshPending={workbench.refreshPending} /></div>
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] gap-3 min-[1200px]:grid-cols-[minmax(300px,320px)_minmax(0,1fr)]">
          <div className={`min-h-0 min-w-0 ${isMobile && mobileView !== "list" ? "hidden" : "flex"}`}>{sessionList}</div>
          <div className={`min-h-0 min-w-0 ${isMobile && mobileView !== "detail" ? "hidden" : "flex"}`}>{sessionDetail}</div>
        </div>
      </div>

      <ForkSessionDrawer
        conversation={active}
        open={forkIndex !== undefined}
        messageIndex={forkIndex}
        onClose={() => setForkIndex(undefined)}
      />
      <ChatAnalysisModal open={workbench.analysisOpen} analysis={active?.analysis} focus={analysisFocus} loading={workbench.analysisLoading} error={workbench.analysisError} onClose={() => workbench.setAnalysisOpen(false)} />
      <CustomerInfo
        conversation={active}
        open={customerInfoOpen}
        onClose={() => setCustomerInfoOpen(false)}
        onGotoContact={() => void workbench.gotoContact()}
        onSaved={() => { void workbench.refreshActiveDetail(); void workbench.reload(); }}
        portraitTags={workbench.portraitTags}
        onTagCreated={workbench.addPortraitTag}
        onPortraitTagsChanged={() => void workbench.reloadPortraitTags()}
      />
      <CardDetailDrawer card={workbench.activeCard} open={Boolean(workbench.activeCard)} onClose={() => workbench.setActiveCardId(undefined)} />
    </>
  );
}
