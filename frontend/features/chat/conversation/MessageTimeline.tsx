"use client";

import { CopyOutlined, ForkOutlined, InfoCircleOutlined, LoadingOutlined, ReloadOutlined, RobotOutlined, ShopOutlined, TranslationOutlined } from "@ant-design/icons";
import { Avatar, Button, Tooltip } from "antd";
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { BusinessCardView } from "@/components/BusinessCardView";
import { TimeText } from "@/components/TimeText";
import { MessageImage } from "./MessageImage";
import { MessageActionSheet, type MessageAction } from "./MessageActionSheet";
import { avatarColorOf, avatarInitialOf } from "@/domain/chat/avatarModel";
import { isTranslatableMessage } from "@/domain/chat/chatModel";
import { formatFullDateTime } from "@/domain/time";
import { renderContentHtml } from "@/domain/render/markdown";
import type { ChatMessage } from "@/types/chatCanonical";

const LONG_PRESS_MS = 500;
const LONG_PRESS_TOLERANCE_PX = 10;

type Sheet = { id: string; text: string; open: boolean };

export function MessageTimeline({
  messages,
  buyerId = "buyer",
  buyerName = "",
  showTranslations = true,
  pendingIds,
  failedIds,
  onTranslate,
  onOpenCard,
  onForkFromMessage,
  conversationId,
  loadImage,
}: {
  messages: ChatMessage[];
  buyerId?: string;
  buyerName?: string;
  showTranslations?: boolean;
  pendingIds?: ReadonlySet<string>;
  failedIds?: ReadonlySet<string>;
  onTranslate?: (message: ChatMessage, force?: boolean) => void;
  onOpenCard: (cardId: string) => void;
  /** Start a simulated session from this message's point in the conversation. */
  onForkFromMessage?: (index: number) => void;
  /** Conversation the messages belong to; image bytes are fetched per message through it. */
  conversationId?: string;
  loadImage?: (conversationId: string, externalMid: string) => Promise<Blob>;
}) {
  const [sheet, setSheet] = useState<Sheet>();
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const origin = useRef({ x: 0, y: 0 });
  const pressing = useRef(false);

  // 行内翻译操作与工具栏一致：任一翻译任务在途时禁用，避免覆盖在途 job。
  const jobBusy = (pendingIds?.size ?? 0) > 0;
  const canStartTranslate = (message: ChatMessage) =>
    Boolean(onTranslate) && isTranslatableMessage(message) && !pendingIds?.has(message.id) && !failedIds?.has(message.id) && !message.translatedContent;

  const cancelPress = () => clearTimeout(timer.current);
  useEffect(() => {
    const pending = timer;
    return () => clearTimeout(pending.current);
  }, []);

  // Touch devices have no hover, so the per-message actions open from a long press instead.
  function startPress(event: PointerEvent<HTMLDivElement>) {
    cancelPress();
    pressing.current = false;
    if (event.pointerType === "mouse") return;
    const target = event.target as HTMLElement;
    if (target.closest("a, button, textarea, input")) return;
    const row = target.closest<HTMLElement>("[data-message-id]");
    if (!row) return;
    setSheet(undefined);
    pressing.current = true;
    origin.current = { x: event.clientX, y: event.clientY };
    timer.current = setTimeout(() => {
      const body = row.querySelector<HTMLElement>("[data-message-text]");
      setSheet({ id: row.dataset.messageId!, text: (body?.innerText || body?.textContent || "").trim(), open: true });
    }, LONG_PRESS_MS);
  }

  function movePress(event: PointerEvent<HTMLDivElement>) {
    if (Math.hypot(event.clientX - origin.current.x, event.clientY - origin.current.y) > LONG_PRESS_TOLERANCE_PX) cancelPress();
  }

  const sheetIndex = sheet ? messages.findIndex((item) => item.id === sheet.id) : -1;
  const sheetMessage = sheetIndex >= 0 ? messages[sheetIndex] : undefined;
  const actions: MessageAction[] = [];
  if (sheet && sheetMessage) {
    if (canStartTranslate(sheetMessage)) {
      actions.push({ key: "translate", label: "翻译", icon: <TranslationOutlined />, disabled: jobBusy, onSelect: () => onTranslate?.(sheetMessage) });
    }
    if (onForkFromMessage) {
      actions.push({ key: "fork", label: "从此开始模拟", icon: <ForkOutlined />, onSelect: () => onForkFromMessage(sheetIndex) });
    }
    if (sheet.text && typeof navigator !== "undefined" && navigator.clipboard) {
      actions.push({ key: "copy", label: "复制", icon: <CopyOutlined />, onSelect: () => void navigator.clipboard.writeText(sheet.text).catch(() => undefined) });
    }
  }

  return (
    <>
    <div
      className="flex w-full flex-col gap-5"
      onPointerDown={startPress}
      onPointerMove={movePress}
      onPointerUp={cancelPress}
      onPointerCancel={cancelPress}
      // Android raises contextmenu on a long press; the sheet replaces it.
      onContextMenu={(event) => { if (pressing.current) event.preventDefault(); }}
    >
      {messages.map((message, index) => {
        // Auto-reception replies (role "system") are sent by our own account:
        // align them with the seller side so the sender is not misread.
        const isSelfSide = message.role === "seller" || message.role === "system";
        const isSystem = message.role === "system" || message.role === "unknown";
        const card = message.card;
        const canTranslate = isTranslatableMessage(message);
        const translation = canTranslate ? message.translatedContent : undefined;
        const pending = canTranslate && Boolean(pendingIds?.has(message.id));
        const failed = canTranslate && Boolean(failedIds?.has(message.id));
        const showTranslation = showTranslations && Boolean(translation);
        const showTranslateAction = canStartTranslate(message);
        return (
          <div key={message.id} data-message-id={message.id} className={`flex ${isSelfSide ? "justify-end" : "justify-start"}`}>
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
              <div className="group/message min-w-0 [@media(hover:none)]:select-none [@media(hover:none)]:[-webkit-touch-callout:none]">
                <div className={`mb-1.5 flex items-center gap-1.5 text-[11px] text-slate-400 ${isSelfSide ? "justify-end" : ""}`}>
                  <TimeText value={message.createdAt} />
                </div>
                <div className={`group relative rounded-2xl px-3 py-2.5 text-sm leading-relaxed [overflow-wrap:anywhere] sm:px-4 ${message.role === "seller" ? "rounded-tr-sm bg-blue-50" : isSystem ? "bg-slate-100 text-slate-600" : "rounded-tl-sm border border-slate-100 bg-white"}`}>
                  {/* Desktop only: hovering the message reveals a button group beside the bubble. Touch devices use the long-press sheet. */}
                  {showTranslateAction || onForkFromMessage ? (
                    <div className={`absolute top-0 z-10 flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover/message:opacity-100 [@media(hover:none)]:hidden ${isSelfSide ? "right-full pr-1.5" : "left-full pl-1.5"}`}>
                      <span className="flex items-center rounded-lg border border-slate-200 bg-white p-0.5 shadow-sm">
                        {showTranslateAction ? (
                          <Tooltip title="翻译本条消息">
                            <Button size="small" type="text" aria-label="翻译本条消息" icon={<TranslationOutlined />} disabled={jobBusy} onClick={() => onTranslate?.(message)} />
                          </Tooltip>
                        ) : null}
                        {onForkFromMessage ? (
                          <Tooltip title="从此开始模拟">
                            <Button size="small" type="text" aria-label="从此开始模拟" icon={<ForkOutlined />} onClick={() => onForkFromMessage(index)} />
                          </Tooltip>
                        ) : null}
                      </span>
                    </div>
                  ) : null}
                  {card ? (
                    <BusinessCardView card={card} compact onClick={() => onOpenCard(card.id)} />
                  ) : message.type === "image" && message.image && conversationId && loadImage ? (
                    <MessageImage conversationId={conversationId} message={message} load={loadImage} />
                  ) : (
                    <div data-message-text className="whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderContentHtml(message.content, "rich-text")} />
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
                      disabled={jobBusy || !onTranslate}
                      onClick={() => onTranslate?.(message)}
                    >
                      翻译失败，点击重试
                    </button>
                  ) : null}
                  {showTranslation && translation ? (
                    <div className="mt-3 border-t border-slate-200/70 pt-2">
                      <div className="flex items-start gap-1.5 text-slate-500">
                        <TranslationOutlined aria-hidden className="mt-[3px] shrink-0 text-xs text-slate-400" />
                        <div className="min-w-0 flex-1 whitespace-pre-wrap break-words" dangerouslySetInnerHTML={renderContentHtml(translation, "rich-text")} />
                        {canTranslate ? (
                          <Tooltip title="重新翻译本条消息">
                            <Button
                              className="!px-1 opacity-100 transition-opacity focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100"
                              size="small"
                              type="text"
                              aria-label="重新翻译本条消息"
                              icon={<ReloadOutlined />}
                              disabled={jobBusy || !onTranslate}
                              onClick={() => onTranslate?.(message, true)}
                            />
                          </Tooltip>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
    <MessageActionSheet
      open={Boolean(sheet?.open && actions.length)}
      detail={sheetMessage ? formatFullDateTime(sheetMessage.createdAt) : undefined}
      actions={actions}
      onClose={() => setSheet((current) => current && { ...current, open: false })}
      onClosed={() => setSheet(undefined)}
    />
    </>
  );
}
