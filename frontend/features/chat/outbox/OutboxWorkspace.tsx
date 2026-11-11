"use client";

import { Button, Popover } from "antd";
import { useRef, useState } from "react";
import { useAccount } from "@/features/account/AccountProvider";
import type { ConversationDetail } from "@/types/chatCanonical";
import { ChatComposer, type ChatComposerProps } from "../workspace/ChatComposer";
import type { useOutbox } from "./useOutbox";
import type { PendingIntent } from "./intentStorage";
import { OutboxTaskStatus } from "./OutboxTaskBubble";
import { isTerminal } from "./outboxModel";

export type DraftSubmission = { id: string; content: string; clear: () => void };

export function OutboxWorkspace({ conversation, outbox, captureDraft, takePendingIntent, ...composer }: Omit<ChatComposerProps, "onSend" | "onFill" | "loading" | "conversationId" | "sendDisabled"> & {
  conversation: ConversationDetail;
  outbox: ReturnType<typeof useOutbox>;
  captureDraft: () => DraftSubmission;
  takePendingIntent?: (content: string) => string | undefined;
}) {
  const { snapshot, blocked } = useAccount();
  const [draftError, setDraftError] = useState<string>();
  const locked = useRef(false);
  const canOperate = !blocked && Boolean(snapshot?.capabilities.operate_client);
  const missing = outbox.intents.filter((intent) => !outbox.tasks.some((task) => task.idempotency_key === intent.key));
  const fills = outbox.tasks.filter((task) => task.action === "test" && task.status !== "cancelled");
  const visibleFills = fills.filter((task, index) => index === 0 || !isTerminal(task));

  async function submit(action: "send" | "test", recovery?: PendingIntent) {
    if (locked.current || outbox.busy || !canOperate) return;
    locked.current = true;
    try {
      setDraftError(undefined);
      const draft = captureDraft();
      const content = recovery?.content ?? draft.content;
      const result = await outbox.submit(content, action, draft.id, recovery ? undefined : takePendingIntent?.(content), recovery);
      if (result && result.status !== "failed" && result.status !== "cancelled"
        && (!recovery || recovery.draftId === draft.id)) draft.clear();
    } catch (error) {
      setDraftError(error instanceof Error ? error.message : "提交失败，草稿已保留");
    } finally { locked.current = false; }
  }

  const error = draftError || (outbox.storageError ? "提交记录不可读或不可写，已暂停新提交。" : outbox.error);
  return <>
    {error || missing.length || visibleFills.length || outbox.notice ? <div className="flex flex-col gap-1 py-2 text-xs text-slate-500" aria-label="发送状态">
      {error ? <div role="alert" className="flex items-center justify-between gap-2 text-amber-700">
        <span>{error}</span><Button size="small" type="text" disabled={blocked} onClick={() => { setDraftError(undefined); void outbox.refresh(true); }}>刷新任务</Button>
      </div> : null}
      {missing.map((intent) => <div key={intent.key} className="flex min-w-0 items-center gap-2 text-amber-700">
        <Popover trigger="click" content={<div className="max-w-80 whitespace-pre-wrap break-words">{intent.content}</div>}>
          <button type="button" className="min-w-0 truncate text-left">提交结果待核对 · {intent.content}</button>
        </Popover>
        <Button size="small" type="text" disabled={!canOperate || outbox.storageError} loading={outbox.busy} onClick={() => void submit(intent.action, intent)}>恢复原提交</Button>
      </div>)}
      {visibleFills.map((task) => <div key={task.id} className="flex min-w-0 items-center gap-2">
        <span>仅填入</span><OutboxTaskStatus task={task} outbox={outbox} />
      </div>)}
      {outbox.notice ? <span role="status">{outbox.notice}</span> : null}
    </div> : <div className="h-2" />}
    <ChatComposer {...composer} conversationId={conversation.id} loading={outbox.busy}
      sendDisabled={outbox.storageError || !conversation.customer.loginId}
      onSend={() => void submit("send")} onFill={() => void submit("test")} />
  </>;
}
