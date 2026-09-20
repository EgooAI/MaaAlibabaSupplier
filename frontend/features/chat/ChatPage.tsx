"use client";

import { ArrowDownOutlined, ArrowLeftOutlined, CheckOutlined, SettingOutlined, UserOutlined } from "@ant-design/icons";
import { Suspense, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Empty, Popover, Spin, Tooltip } from "antd";
import { useRouter, useSearchParams } from "next/navigation";
import { inboxQueryFromUrl } from "@/domain/chat/inboxModel";
import { statusLabel } from "@/domain/chat/chatModel";
import { ConversationFilters, ConversationPaging } from "./conversation/ConversationFilters";
import { InboxBadges } from "./conversation/InboxBadges";
import { CardDetailDrawer } from "@/components/CardDetailDrawer";
import { SessionListPanel } from "@/components/SessionListPanel";
import { useSplitSessionMobile } from "@/components/SplitSessionLayout";
import { useStickToBottom } from "@/components/useStickToBottom";
import { ConversationList } from "./conversation/ConversationList";
import { CustomerInfo } from "./conversation/CustomerInfo";
import { MessageTimeline } from "./conversation/MessageTimeline";
import { TranslationToolbar } from "./conversation/TranslationToolbar";
import { useChatWorkbench } from "./hooks/useChatWorkbench";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { AssistantSuggestionModal } from "./modals/AssistantSuggestionModal";
import { ChatAnalysisModal } from "./modals/ChatAnalysisModal";
import { OutboxWorkspace } from "./outbox/OutboxWorkspace";
import { useAccount } from "@/features/account/AccountProvider";
import { SyncStatus } from "@/features/account/SyncStatus";

type AnalysisFocus = "intent" | "stage";

export function ChatPage() {
  const { snapshot, blocked, suspended, generation } = useAccount();
  if ((blocked && !suspended) || !snapshot?.capabilities.read_chat) return <DataDirBanner />;
  return <Suspense fallback={<Spin />}><ChatWorkspace key={`${snapshot.account.epoch}:${generation}`} /></Suspense>;
}

function ChatWorkspace() {
  const { snapshot, blocked } = useAccount();
  const router = useRouter();
  const { isMobile, mobileView, setMobileView } = useSplitSessionMobile();
  const params = useSearchParams().toString();
  const workbench = useChatWorkbench(inboxQueryFromUrl(new URLSearchParams(params)));
  const canUseAi = !blocked && Boolean(snapshot?.capabilities.use_ai);
  const { changeQuery } = workbench;
  const previousParams = useRef(params);
  useEffect(() => {
    if (previousParams.current === params) return;
    previousParams.current = params;
    changeQuery(inboxQueryFromUrl(new URLSearchParams(params)));
  }, [params, changeQuery]);
  const active = workbench.activeConversation;
  const [customerInfoOpen, setCustomerInfoOpen] = useState(false);
  const [analysisFocus, setAnalysisFocus] = useState<AnalysisFocus>("intent");
  const { scrollRef, showJumpButton, handleScroll, scrollToBottom } = useStickToBottom({ sessionKey: active?.id ?? "", followKey: active?.messages.length ?? 0 });

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
      extra={<Tooltip title="管理会话"><Button type="text" size="small" aria-label="管理会话" icon={<SettingOutlined />} onClick={() => router.push("/batch")} /></Tooltip>}
    >
      <ConversationFilters key={params} query={inboxQueryFromUrl(new URLSearchParams(params))} onChange={workbench.changeQuery} />
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
          {active && (active.customer.company || active.customer.country) ? <div className="truncate text-xs font-normal text-slate-500">{[active.customer.company, active.customer.country].filter(Boolean).join(" · ")}</div> : null}
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
              <span className={active.isOverdue ? "text-red-600" : active.replyState === "needs_reply" || active.uncertain ? "text-amber-700" : "text-slate-500"}>{active.isOverdue ? "超时" : active.uncertain ? "待核实" : statusLabel(active.replyState)}</span>
              {active.unreadCount > 0 ? <span className="h-1.5 w-1.5 rounded-full bg-blue-500" aria-label={`本工作台未读 ${active.unreadCount}`} /> : null}
            </Button>
          </Popover> : null}
          <Tooltip title="客户信息"><Button type="text" icon={<UserOutlined />} aria-label="客户信息" onClick={() => setCustomerInfoOpen(true)} disabled={!active}><span className="hidden sm:inline">客户信息</span></Button></Tooltip>
        </div>
      }
      className="flex h-full min-h-0 w-full flex-col"
      styles={{ header: { padding: "0 16px", flexShrink: 0 }, body: { padding: 0 } }}
      classNames={{ body: "flex min-h-0 flex-1 flex-col overflow-hidden" }}
    >
      {active ? (
        <div className="flex min-h-0 flex-1 flex-col">
          {workbench.translationNotice ? <Alert type="warning" showIcon title={workbench.translationNotice} action={<Button size="small" onClick={() => void workbench.reconcileTranslations()}>查询缓存</Button>} /> : null}
          {workbench.translationStats.total > 0 ? (
            <TranslationToolbar
              visible={workbench.translationVisible}
              onToggleVisible={workbench.toggleTranslation}
              translatedCount={workbench.translationStats.translated}
              untranslatedCount={workbench.translationStats.untranslated}
              pendingCount={workbench.translationStats.pending}
              canTranslate={canUseAi}
              onTranslateMissing={workbench.translateMissing}
              onRetranslateAll={workbench.retranslateConversation}
            />
          ) : null}
          <div className="relative flex min-h-0 flex-1 flex-col">
            {workbench.detailLoading ? (
              <div className="flex flex-1 items-center justify-center"><Spin /></div>
            ) : (
              <div ref={scrollRef} onScroll={handleScroll} className="app-scroll min-h-0 flex-1 overflow-y-auto bg-slate-50/60 p-3 sm:p-5">
                {!active.messages.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无聊天消息" /> : null}
                <MessageTimeline
                  messages={active.messages}
                  buyerId={active.customer.id}
                  buyerName={active.customer.name}
                  showTranslations={workbench.translationVisible}
                  pendingIds={workbench.translationPendingIds}
                  failedIds={workbench.translationFailedIds}
                  onTranslate={canUseAi ? (item, force) => void workbench.translateMessages([item], { force }) : undefined}
                  onOpenCard={workbench.setActiveCardId}
                />
              </div>
            )}
            {showJumpButton ? (
              <div className="absolute right-4 bottom-4 z-10">
                <Tooltip title="回到底部" placement="left">
                  <Button
                    shape="circle"
                    aria-label="回到底部"
                    icon={<ArrowDownOutlined />}
                    className="shadow-md"
                    onClick={() => scrollToBottom(true)}
                  />
                </Tooltip>
              </div>
            ) : null}
          </div>
          <div className="shrink-0 border-t border-slate-100 px-3 pb-3 sm:px-4">
            <OutboxWorkspace
              key={active.id}
              conversation={active}
              value={workbench.draft}
              onChange={workbench.setDraft}
              onOpenSuggestions={workbench.openSuggestions}
              onOpenIntentAnalysis={() => void openAnalysis("intent")}
              onOpenStageAnalysis={() => void openAnalysis("stage")}
            />
          </div>
        </div>
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

      <AssistantSuggestionModal open={workbench.suggestionOpen} suggestions={workbench.suggestions} error={workbench.suggestionError} onClose={() => workbench.setSuggestionOpen(false)} onInsert={workbench.insertSuggestion} />
      <ChatAnalysisModal open={workbench.analysisOpen} analysis={active?.analysis} focus={analysisFocus} loading={workbench.analysisLoading} error={workbench.analysisError} onClose={() => workbench.setAnalysisOpen(false)} />
      <CustomerInfo conversation={active} open={customerInfoOpen} onClose={() => setCustomerInfoOpen(false)} onGotoContact={() => void workbench.gotoContact()} />
      <CardDetailDrawer card={workbench.activeCard} open={Boolean(workbench.activeCard)} onClose={() => workbench.setActiveCardId(undefined)} />
    </>
  );
}
