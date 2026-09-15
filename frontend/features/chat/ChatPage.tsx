"use client";

import { ArrowDownOutlined, ArrowLeftOutlined, SettingOutlined } from "@ant-design/icons";
import { useState } from "react";
import { Button, Card, Space, Spin, Typography } from "antd";
import { useRouter } from "next/navigation";
import { CardDetailDrawer } from "@/components/CardDetailDrawer";
import { SessionListPanel } from "@/components/SessionListPanel";
import { SplitSessionLayout, useSplitSessionMobile } from "@/components/SplitSessionLayout";
import { useStickToBottom } from "@/components/useStickToBottom";
import { ConversationList } from "./conversation/ConversationList";
import { CustomerInfo } from "./conversation/CustomerInfo";
import { MessageTimeline } from "./conversation/MessageTimeline";
import { useChatWorkbench } from "./hooks/useChatWorkbench";
import { AssistantSuggestionModal } from "./modals/AssistantSuggestionModal";
import { ChatAnalysisModal } from "./modals/ChatAnalysisModal";
import { ChatComposer } from "./workspace/ChatComposer";

type AnalysisFocus = "intent" | "stage";

export function ChatPage() {
  const router = useRouter();
  const { isMobile, mobileView, setMobileView } = useSplitSessionMobile();
  const workbench = useChatWorkbench();
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
        <Space>
          {isMobile ? <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => setMobileView("list")}>返回列表</Button> : null}
          <Button type="primary" onClick={() => setCustomerInfoOpen(true)} disabled={!active}>客户信息</Button>
        </Space>
      }
      className="flex h-full min-h-0 w-full flex-col"
      classNames={{ body: "flex min-h-0 flex-1 flex-col" }}
    >
      {active ? (
        <div className="flex min-h-0 flex-1 flex-col gap-6">
          <div className="relative flex min-h-0 flex-1 flex-col">
            {workbench.detailLoading ? (
              <div className="flex flex-1 items-center justify-center"><Spin /></div>
            ) : (
              <div ref={scrollRef} onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto pr-2">
                <MessageTimeline
                  messages={active.messages}
                  buyerId={active.customer.id}
                  showTranslations={workbench.translationVisible}
                  onRegenerate={(item) => workbench.translate(item, true)}
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
            <ChatComposer
              value={workbench.draft}
              onChange={workbench.setDraft}
              translationVisible={workbench.translationVisible}
              onToggleTranslation={workbench.toggleTranslation}
              onRetranslate={() => void workbench.retranslateConversation()}
              onOpenSuggestions={workbench.openSuggestions}
              onOpenIntentAnalysis={() => void openAnalysis("intent")}
              onOpenStageAnalysis={() => void openAnalysis("stage")}
              loading={workbench.sending}
              onSend={workbench.sendMessage}
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
      <SplitSessionLayout list={sessionList} detail={sessionDetail} mobileView={mobileView} />

      <AssistantSuggestionModal open={workbench.suggestionOpen} suggestions={workbench.suggestions} onClose={() => workbench.setSuggestionOpen(false)} onInsert={workbench.insertSuggestion} />
      <ChatAnalysisModal open={workbench.analysisOpen} analysis={active?.analysis} focus={analysisFocus} loading={workbench.analysisLoading} error={workbench.analysisError} onClose={() => workbench.setAnalysisOpen(false)} />
      <CustomerInfo conversation={active} open={customerInfoOpen} onClose={() => setCustomerInfoOpen(false)} onGotoContact={() => void workbench.gotoContact()} />
      <CardDetailDrawer card={workbench.activeCard} open={Boolean(workbench.activeCard)} onClose={() => workbench.setActiveCardId(undefined)} />
    </>
  );
}
