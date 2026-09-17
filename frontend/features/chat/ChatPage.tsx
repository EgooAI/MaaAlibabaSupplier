"use client";

import { ArrowDownOutlined, ArrowLeftOutlined, SettingOutlined } from "@ant-design/icons";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button, Card, Space, Spin, Typography } from "antd";
import { useRouter, useSearchParams } from "next/navigation";
import { inboxQueryFromUrl } from "@/domain/chat/inboxModel";
import { ConversationFilters, ConversationPaging } from "./conversation/ConversationFilters";
import { InboxBadges } from "./conversation/InboxBadges";
import { CardDetailDrawer } from "@/components/CardDetailDrawer";
import { SessionListPanel } from "@/components/SessionListPanel";
import { SplitSessionLayout, useSplitSessionMobile } from "@/components/SplitSessionLayout";
import { useStickToBottom } from "@/components/useStickToBottom";
import { ConversationList } from "./conversation/ConversationList";
import { CustomerInfo } from "./conversation/CustomerInfo";
import { MessageTimeline } from "./conversation/MessageTimeline";
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
      extra={<Button type="link" size="small" icon={<SettingOutlined />} onClick={() => router.push("/batch")}>管理会话</Button>}
    >
      <ConversationFilters key={params} query={inboxQueryFromUrl(new URLSearchParams(params))} onChange={workbench.changeQuery} />
      <ConversationPaging page={workbench.page} pending={workbench.pagePending} onChange={workbench.changePage} />
      <ConversationList
        conversations={workbench.conversations}
        activeId={active?.id}
        groupMode={workbench.groupMode}
        onGroupModeChange={workbench.setGroupMode}
        onSelect={handleSelectConversation}
      />
    </SessionListPanel>
  );
  const sessionDetail = (
    <Card
      title={active ? `${active.customer.name} · ${active.customer.company}` : "消息时间线"}
      extra={
        <Space wrap>
          {isMobile ? <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => setMobileView("list")}>返回列表</Button> : null}
          <Button type="primary" onClick={() => setCustomerInfoOpen(true)} disabled={!active}>客户信息</Button>
        </Space>
      }
      className="flex h-full min-h-0 w-full flex-col"
      classNames={{ body: "flex min-h-0 flex-1 flex-col" }}
    >
      {active ? (
        <div className="flex min-h-0 flex-1 flex-col gap-6">
          <Space wrap>
            <InboxBadges conversation={active} />
            <Button size="small" disabled={blocked || !active.readSnapshot} loading={workbench.markingRead} onClick={() => void workbench.markRead()}>标记工作台已读</Button>
          </Space>
          <div className="relative flex min-h-0 flex-1 flex-col">
            {workbench.detailLoading ? (
              <div className="flex flex-1 items-center justify-center"><Spin /></div>
            ) : (
              <div ref={scrollRef} onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto pr-2">
                <MessageTimeline
                  messages={active.messages}
                  buyerId={active.customer.id}
                  buyerName={active.customer.name}
                  showTranslations={workbench.translationVisible}
                  onRegenerate={!blocked && snapshot?.capabilities.use_ai ? (item) => workbench.translate(item, true) : undefined}
                  onOpenCard={workbench.setActiveCardId}
                />
              </div>
            )}
            {showJumpButton ? (
              <Button
                size="small"
                shape="round"
                icon={<ArrowDownOutlined />}
                className="absolute bottom-2 left-1/2 -translate-x-1/2 shadow"
                onClick={() => scrollToBottom(true)}
              >
                回到底部
              </Button>
            ) : null}
          </div>
          <div className="shrink-0">
            <OutboxWorkspace
              key={active.id}
              conversation={active}
              value={workbench.draft}
              onChange={workbench.setDraft}
              translationVisible={workbench.translationVisible}
              onToggleTranslation={workbench.toggleTranslation}
              onRetranslate={() => void workbench.retranslateConversation()}
              onOpenSuggestions={workbench.openSuggestions}
              onOpenIntentAnalysis={() => void openAnalysis("intent")}
              onOpenStageAnalysis={() => void openAnalysis("stage")}
            />
          </div>
        </div>
      ) : (
        <Typography.Text>请选择一个会话</Typography.Text>
      )}
    </Card>
  );

  return (
    <>
      <DataDirBanner />
      <div className="mb-2"><SyncStatus refreshError={workbench.refreshError} refreshPending={workbench.refreshPending} /></div>
      <SplitSessionLayout list={sessionList} detail={sessionDetail} mobileView={mobileView} />

      <AssistantSuggestionModal open={workbench.suggestionOpen} suggestions={workbench.suggestions} onClose={() => workbench.setSuggestionOpen(false)} onInsert={workbench.insertSuggestion} />
      <ChatAnalysisModal open={workbench.analysisOpen} analysis={active?.analysis} focus={analysisFocus} loading={workbench.analysisLoading} error={workbench.analysisError} onClose={() => workbench.setAnalysisOpen(false)} />
      <CustomerInfo conversation={active} open={customerInfoOpen} onClose={() => setCustomerInfoOpen(false)} onGotoContact={() => void workbench.gotoContact()} />
      <CardDetailDrawer card={workbench.activeCard} open={Boolean(workbench.activeCard)} onClose={() => workbench.setActiveCardId(undefined)} />
    </>
  );
}
