"use client";

import { InfoCircleOutlined, LoadingOutlined, RobotOutlined, ShopOutlined } from "@ant-design/icons";
import { Avatar, Button } from "antd";
import { BusinessCardView } from "@/components/BusinessCardView";
import { avatarColorOf, avatarInitialOf } from "@/domain/chat/avatarModel";
import { renderMessageHtml } from "@/domain/chat/messageHtml";
import type { ChatMessage } from "@/types/chatCanonical";

export function MessageTimeline({
  messages,
  buyerId = "buyer",
  buyerName = "",
  showTranslations = true,
  pendingIds,
  failedIds,
  onTranslate,
  onOpenCard,
}: {
  messages: ChatMessage[];
  buyerId?: string;
  buyerName?: string;
  showTranslations?: boolean;
  pendingIds?: ReadonlySet<string>;
  failedIds?: ReadonlySet<string>;
  onTranslate?: (message: ChatMessage, force?: boolean) => void;
  onOpenCard: (cardId: string) => void;
}) {
  return (
    <div className="flex w-full flex-col gap-5">
      {messages.map((message) => {
        // Auto-reception replies (role "system") are sent by our own account:
        // align them with the seller side so the sender is not misread.
        const isSelfSide = message.role === "seller" || message.role === "system";
        const isSystem = message.role === "system" || message.role === "unknown";
        const card = message.card;
        const canTranslate = message.role !== "card" && !card && Boolean(message.content.trim());
        const pending = canTranslate && Boolean(pendingIds?.has(message.id));
        const failed = canTranslate && Boolean(failedIds?.has(message.id));
        const showTranslation = canTranslate && showTranslations && Boolean(message.translatedContent);
        const showTranslateAction = canTranslate && !showTranslation && !pending && !message.translatedContent && onTranslate;
        return (
          <div key={message.id} className={`flex ${isSelfSide ? "justify-end" : "justify-start"}`}>
            <div className={`flex min-w-0 max-w-full items-start gap-2 sm:max-w-[88%] sm:gap-3 ${isSelfSide ? "flex-row-reverse" : ""}`}>
              <div className="hidden shrink-0 sm:block">
              {message.role === "seller" || message.role === "system" ? (
                <Avatar icon={isSystem ? <InfoCircleOutlined /> : <ShopOutlined />} style={{ backgroundColor: isSystem ? "#b45309" : "#1677ff" }} />
              ) : isSystem ? (
                <Avatar icon={<InfoCircleOutlined />} style={{ backgroundColor: "#b45309" }} />
              ) : message.role === "card" ? (
                <Avatar icon={<RobotOutlined />} style={{ backgroundColor: "#64748b" }} />
              ) : (
                <Avatar style={{ backgroundColor: avatarColorOf(buyerId) }}>{avatarInitialOf(buyerName)}</Avatar>
              )}
              </div>
              <div className="min-w-0">
                <div className={`mb-1.5 text-[11px] text-slate-400 ${isSelfSide ? "text-right" : ""}`}>{message.createdAt}</div>
                <div className={`group rounded-2xl px-3 py-2.5 text-sm leading-relaxed [overflow-wrap:anywhere] sm:px-4 ${message.role === "seller" ? "rounded-tr-sm bg-blue-50" : isSystem ? "bg-slate-100 text-slate-600" : "rounded-tl-sm border border-slate-100 bg-white"}`}>
                  {card ? (
                    <BusinessCardView card={card} compact onClick={() => onOpenCard(card.id)} />
                  ) : (
                    <div className="whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderMessageHtml(message.content)} />
                  )}
                  {pending ? (
                    <div className="mt-2 flex items-center gap-1.5 text-xs text-slate-400" role="status">
                      <LoadingOutlined aria-hidden />
                      <span>翻译中…</span>
                    </div>
                  ) : failed ? (
                    <button
                      type="button"
                      className="mt-2 text-xs text-red-500 underline-offset-2 hover:underline"
                      disabled={!onTranslate}
                      onClick={() => onTranslate?.(message)}
                    >
                      翻译失败，点击重试
                    </button>
                  ) : showTranslateAction ? (
                    <button
                      type="button"
                      className="mt-2 text-xs text-blue-500 underline-offset-2 hover:underline"
                      onClick={() => onTranslate?.(message)}
                    >
                      翻译
                    </button>
                  ) : null}
                  {showTranslation ? (
                    <div className="mt-3 border-t border-slate-200/70 pt-2 text-slate-500">
                      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
                        <span>译文</span>
                        {canTranslate ? (
                          <Button
                            className="!px-1 !text-xs opacity-100 transition-opacity focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                            size="small"
                            type="text"
                            aria-label="重新翻译本条消息"
                            disabled={!onTranslate}
                            onClick={() => onTranslate?.(message, true)}
                          >
                            重新翻译
                          </Button>
                        ) : null}
                      </div>
                      <div className="whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderMessageHtml(message.translatedContent!)} />
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
