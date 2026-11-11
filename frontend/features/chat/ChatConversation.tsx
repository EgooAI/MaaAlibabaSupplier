"use client";

import { ArrowDownOutlined } from "@ant-design/icons";
import { Alert, Button, Empty, Spin, Tooltip } from "antd";
import { useStickToBottom } from "@/components/useStickToBottom";
import { useAccount } from "@/features/account/AccountProvider";
import type { ConversationDetail } from "@/types/chatCanonical";
import { MessageTimeline } from "./conversation/MessageTimeline";
import { TranslationToolbar } from "./conversation/TranslationToolbar";
import type { useChatWorkbench } from "./hooks/useChatWorkbench";
import { OutboxWorkspace } from "./outbox/OutboxWorkspace";
import { pendingBubbles } from "./outbox/outboxModel";
import { useOutbox } from "./outbox/useOutbox";
import { useSuggestions } from "./suggestions/useSuggestions";
import { SuggestionAgentSelect, SuggestionPanel } from "./suggestions/SuggestionPanel";

export function ChatConversation({ active, workbench, onFork, onAnalysis }: {
  active: ConversationDetail;
  workbench: ReturnType<typeof useChatWorkbench>;
  onFork: (index: number) => void;
  onAnalysis: (focus: "intent" | "stage") => void;
}) {
  const { snapshot, blocked } = useAccount();
  const canUseAi = !blocked && Boolean(snapshot?.capabilities.use_ai);
  const outbox = useOutbox(active.id);
  const suggestions = useSuggestions(active, workbench.captureDraft, workbench.insertSuggestion);
  const tasks = pendingBubbles(outbox.tasks, active.messages, snapshot!.account.self_ali_id, active.id);
  const { scrollRef, showJumpButton, handleScroll, scrollToBottom } = useStickToBottom({
    sessionKey: active.id, followKey: active.messages.length + tasks.length,
  });

  return <div className="flex min-h-0 flex-1 flex-col">
    {workbench.translationNotice ? <Alert type="warning" showIcon title={workbench.translationNotice} action={<Button size="small" onClick={() => void workbench.reconcileTranslations()}>查询缓存</Button>} /> : null}
    {workbench.translationStats.total > 0 ? <TranslationToolbar
      visible={workbench.translationVisible} onToggleVisible={workbench.toggleTranslation}
      translatedCount={workbench.translationStats.translated} untranslatedCount={workbench.translationStats.untranslated}
      pendingCount={workbench.translationStats.pending} canTranslate={canUseAi}
      onTranslateMissing={workbench.translateMissing} onRetranslateAll={workbench.retranslateConversation}
    /> : null}
    <div className={`grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_auto_auto] ${suggestions.open ? "2xl:grid-cols-[minmax(0,1fr)_420px] 2xl:grid-rows-[minmax(0,1fr)_auto]" : ""}`}>
    <div className="relative row-start-1 flex min-h-0 min-w-0 flex-col">
      {workbench.detailLoading ? <div className="flex flex-1 items-center justify-center"><Spin /></div> :
        <div ref={scrollRef} onScroll={handleScroll} className="app-scroll min-h-0 flex-1 overflow-y-auto bg-slate-50/60 p-3 sm:p-5">
          {!active.messages.length && !tasks.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无聊天消息" /> : null}
          <MessageTimeline messages={active.messages} conversationId={active.id} loadImage={workbench.loadMessageImage}
            buyerId={active.customer.id} buyerName={active.customer.name} showTranslations={workbench.translationVisible}
            pendingIds={workbench.translationPendingIds} failedIds={workbench.translationFailedIds}
            onTranslate={canUseAi ? (item, force) => void workbench.translateMessages([item], { force }) : undefined}
            onOpenCard={workbench.setActiveCardId} onForkFromMessage={canUseAi ? onFork : undefined}
            sending={{ tasks, outbox }} />
        </div>}
      {showJumpButton ? <div className="absolute right-4 bottom-4 z-10"><Tooltip title="回到底部" placement="left">
        <Button shape="circle" aria-label="回到底部" icon={<ArrowDownOutlined />} className="shadow-md" onClick={() => scrollToBottom(true)} />
      </Tooltip></div> : null}
    </div>
    {suggestions.open ? <aside className="app-scroll row-start-2 max-h-[50dvh] min-h-0 min-w-0 overflow-y-auto border-t border-slate-100 bg-white 2xl:col-start-2 2xl:row-span-2 2xl:row-start-1 2xl:max-h-none 2xl:border-t-0 2xl:border-l">
      <SuggestionPanel workspace={suggestions} canUseAi={canUseAi} blocked={blocked} />
    </aside> : null}
    <div className="row-start-3 min-w-0 border-t border-slate-100 px-3 pb-3 sm:px-4 2xl:col-start-1 2xl:row-start-2">
      <OutboxWorkspace conversation={active} outbox={outbox} value={workbench.draft} onChange={workbench.setDraft}
        captureDraft={workbench.captureDraft} takePendingIntent={workbench.takePendingIntent}
        onOpenSuggestions={() => void suggestions.show(true)} onOpenIntentAnalysis={() => onAnalysis("intent")}
        suggestionAgent={<SuggestionAgentSelect workspace={suggestions} disabled={blocked || suggestions.saving} />}
        onOpenStageAnalysis={() => onAnalysis("stage")} />
    </div>
    </div>
  </div>;
}
