"use client";

import { Button, Modal, Popover } from "antd";
import { useRef, useState } from "react";
import { useAccount } from "@/features/account/AccountProvider";
import type { ConversationDetail } from "@/types/chatCanonical";
import { ChatComposer, type ChatComposerProps } from "../workspace/ChatComposer";
import type { useOutbox } from "./useOutbox";
import type { PendingIntent } from "./intentStorage";
import type { MessageFormat } from "@/types/chatOperations";
import { containsHtmlElements } from "@/domain/chat/messageFormat";
import { captureAccount } from "@/services/accountSession";

export type DraftSubmission = { id: string; content: string; clear: () => void };
type FormatChoice = { draft: DraftSubmission; action: "send" | "test"; intentId?: string; ticket: ReturnType<typeof captureAccount> };

export function OutboxWorkspace({ conversation, outbox, captureDraft, getPendingIntent, ...composer }: Omit<ChatComposerProps, "onSend" | "onFill" | "loading" | "conversationId" | "sendDisabled"> & {
  conversation: ConversationDetail;
  outbox: ReturnType<typeof useOutbox>;
  captureDraft: () => DraftSubmission;
  getPendingIntent?: (content: string) => string | undefined;
}) {
  const { snapshot, blocked } = useAccount();
  const [draftError, setDraftError] = useState<string>();
  const locked = useRef(false);
  const [formatChoice, setFormatChoice] = useState<FormatChoice | null>(null);
  const pendingChoice = useRef<FormatChoice | null>(null);
  const canOperate = !blocked && Boolean(snapshot?.capabilities.operate_client);
  const missing = outbox.intents.filter((intent) => !outbox.tasks.some((task) => task.idempotency_key === intent.key));

  async function submit(action: "send" | "test", recovery?: PendingIntent, prepared?: FormatChoice, contentFormat: MessageFormat = "text") {
    if (locked.current || pendingChoice.current || outbox.busy || !canOperate) return;
    locked.current = true;
    try {
      setDraftError(undefined);
      const ticket = prepared?.ticket ?? captureAccount();
      ticket.assertCurrent();
      const draft = prepared?.draft ?? captureDraft();
      const prior = recovery ?? outbox.intents.findLast((intent) => intent.draftId === draft.id && intent.action === action);
      const content = prior?.content ?? draft.content;
      const intentId = prepared ? prepared.intentId : prior ? undefined : getPendingIntent?.(content);
      if (!prior && !prepared && containsHtmlElements(content)) {
        pendingChoice.current = { draft, action, intentId, ticket };
        setFormatChoice(pendingChoice.current);
        return;
      }
      const result = await outbox.submit(content, action, draft.id, intentId, prior, prior ? prior.contentFormat ?? "text" : contentFormat);
      if (result && result.status !== "failed" && result.status !== "cancelled"
        && (!recovery || recovery.draftId === draft.id)) draft.clear();
    } catch (error) {
      setDraftError(error instanceof Error ? error.message : "提交失败，草稿已保留");
    } finally { locked.current = false; }
  }

  function closeFormatChoice() {
    pendingChoice.current = null;
    setFormatChoice(null);
  }

  function chooseFormat(format: MessageFormat) {
    const choice = pendingChoice.current;
    closeFormatChoice();
    if (choice) void submit(choice.action, undefined, choice, format);
  }

  const error = draftError || (outbox.storageError ? "提交记录不可读或不可写，已暂停新提交。" : outbox.error);
  return <>
    <Modal title="检测到 HTML 元素" open={Boolean(formatChoice)} onCancel={closeFormatChoice} destroyOnHidden
      footer={<>
        <Button disabled={!canOperate} onClick={() => chooseFormat("text")}>以纯文本发送</Button>
        <Button type="primary" disabled={!canOperate} onClick={() => chooseFormat("html")}>以 HTML 发送</Button>
      </>}>
      是否按照 HTML 格式发送？
    </Modal>
    {error || missing.length || outbox.notice ? <div className="flex flex-col gap-1 py-2 text-xs text-slate-500" aria-label="发送状态">
      {error ? <div role="alert" className="flex items-center justify-between gap-2 text-amber-700">
        <span>{error}</span><Button size="small" type="text" disabled={blocked} onClick={() => { setDraftError(undefined); void outbox.refresh(true); }}>刷新任务</Button>
      </div> : null}
      {missing.map((intent) => <div key={intent.key} className="flex min-w-0 items-center gap-2 text-amber-700">
        <Popover trigger="click" content={<div className="max-w-80 whitespace-pre-wrap break-words">{intent.content}</div>}>
          <button type="button" className="min-w-0 truncate text-left">提交结果待核对 · {intent.content}</button>
        </Popover>
        <Button size="small" type="text" disabled={!canOperate || outbox.storageError} loading={outbox.busy} onClick={() => void submit(intent.action, intent)}>恢复原提交</Button>
      </div>)}
      {outbox.notice ? <span role="status">{outbox.notice}</span> : null}
    </div> : <div className="h-2" />}
    <ChatComposer {...composer} conversationId={conversation.id} loading={outbox.busy}
      sendDisabled={outbox.storageError || !conversation.customer.loginId || Boolean(formatChoice)}
      onSend={() => void submit("send")} onFill={() => void submit("test")} />
  </>;
}
