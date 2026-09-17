"use client";

import { Alert, Button, Modal, Space, Tag, Typography } from "antd";
import { useState } from "react";
import { useAccount } from "@/features/account/AccountProvider";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { OutboxTask } from "@/types/chatOperations";
import { ChatComposer, type ChatComposerProps } from "../workspace/ChatComposer";
import { canCancel, canRetry, isTerminal, outboxLabels } from "./outboxModel";
import { useOutbox } from "./useOutbox";
import { ScreenshotConfirmation } from "./ScreenshotConfirmation";
import type { PendingIntent } from "./intentStorage";

export function OutboxWorkspace({ conversation, ...composer }: Omit<ChatComposerProps, "onSend" | "loading"> & { conversation: ConversationDetail }) {
  const { snapshot, blocked } = useAccount();
  const outbox = useOutbox(conversation.id);
  const [submission, setSubmission] = useState<{ content: string; action?: "send" | "test"; newIntent?: boolean; intent?: PendingIntent }>();
  const [reviewId, setReviewId] = useState<string>();
  const [retry, setRetry] = useState<OutboxTask>();
  const canOperate = !blocked && Boolean(snapshot?.capabilities.operate_client);
  const review = outbox.tasks.find((task) => task.id === reviewId);
  const missing = outbox.intents.filter((intent) => !outbox.tasks.some((task) => task.idempotency_key === intent.key));

  async function submit(action: "send" | "test") {
    if (!submission) return;
    if (await outbox.submit(submission.content, action, submission.intent)) setSubmission((current) => current === submission ? undefined : current);
  }

  return <>
    <section aria-label="会话发送任务" className="mb-3 max-h-64 overflow-y-auto rounded border border-slate-200 p-3">
      <Space wrap><Typography.Text strong>发送任务</Typography.Text><Button size="small" disabled={blocked} onClick={() => void outbox.refresh(true)}>刷新任务</Button></Space>
      <Typography.Paragraph type="secondary">任务由后台保存；切换会话或刷新页面后可继续查看。草稿始终保留。</Typography.Paragraph>
      <Typography.Paragraph type="secondary">相同内容会恢复已有任务；需要再次发送请点击“新建发送”。</Typography.Paragraph>
      {outbox.error && <Alert type="warning" title={outbox.error} />}
      {outbox.storageError && <Alert type="error" title="浏览器提交记录不可读或不可写，已禁止新提交。请恢复存储后刷新任务；草稿仍保留。" />}
      {blocked && <Alert type="warning" title="账号状态暂不可用，已保留任务记录，暂停操作。" />}
      {!outbox.tasks.length && !missing.length && <Typography.Text type="secondary">暂无发送任务</Typography.Text>}
      {missing.map((intent) => <div key={intent.key} className="my-2 border-t border-slate-200 pt-2">
        <Typography.Text>提交记录待核对 · {intent.action === "test" ? "仅填入" : "发送"}</Typography.Text>
        <pre className="whitespace-pre-wrap break-words">{intent.content}</pre>
        <Button disabled={!canOperate || outbox.storageError} loading={outbox.busy} onClick={() => setSubmission({ content: intent.content, action: intent.action, intent })}>恢复原提交</Button>
      </div>)}
      {outbox.tasks.map((task) => <article key={task.id} className="my-2 border-t border-slate-200 pt-2" aria-label={`任务 ${task.id}`}>
        <Space wrap><Tag>{outboxLabels[task.status]}</Tag><Typography.Text>{task.action === "test" ? "仅填入" : "发送"} · {task.login_id} · 第 {task.attempt} 次</Typography.Text></Space>
        <div className="text-xs text-slate-500">{new Date(task.created_at * 1000).toLocaleString()} · 阶段：{task.phase || task.status}</div>
        <pre className="whitespace-pre-wrap break-words">{task.content}</pre>
        {task.reason && <Typography.Paragraph>{task.reason}</Typography.Paragraph>}
        {task.status === "observed" && <Typography.Paragraph>仅在本地数据中发现匹配消息，不代表平台送达或对方已收到。</Typography.Paragraph>}
        {(task.status === "unknown" || task.may_have_sent) && <Typography.Paragraph>可能已经发送，请在客户端核对实际结果，禁止自动重试。</Typography.Paragraph>}
        <Space wrap>
          {task.status === "awaiting_confirmation" && <Button disabled={!canOperate} onClick={() => setReviewId(task.id)}>查看截图并确认联系人</Button>}
          {canCancel(task) && <Button disabled={blocked} loading={outbox.busy} onClick={() => void outbox.mutate(task, "cancel")}>取消任务</Button>}
          {canRetry(task) && <Button disabled={!canOperate} loading={outbox.busy} onClick={() => setRetry(task)}>重试并重新截图</Button>}
          {isTerminal(task) && <Button disabled={!canOperate || outbox.storageError || outbox.busy || Boolean(submission)} onClick={() => setSubmission({ content: task.content, action: task.action, newIntent: true, intent: { key: crypto.randomUUID(), content: task.content, action: task.action } })}>新建发送</Button>}
        </Space>
      </article>)}
    </section>
    <ChatComposer {...composer} loading={outbox.busy} onSend={() => setSubmission({ content: composer.value.trim() })} />
    <Modal title={submission?.newIntent ? "新建发送：再次确认" : "确认收件人与提交内容"} open={Boolean(submission)} onCancel={() => setSubmission(undefined)} footer={<Space wrap>
      <Button onClick={() => setSubmission(undefined)}>返回编辑</Button>
      {(!submission?.action || submission.action === "test") && <Button disabled={!canOperate || outbox.storageError || !conversation.customer.loginId} loading={outbox.busy} onClick={() => void submit("test")}>确认收件人，开始搜索（仅填入）</Button>}
      {(!submission?.action || submission.action === "send") && <Button type="primary" disabled={!canOperate || outbox.storageError || !conversation.customer.loginId} loading={outbox.busy} onClick={() => void submit("send")}>确认收件人，开始搜索（发送）</Button>}
    </Space>}>
      <Typography.Paragraph>卖家：{snapshot?.account.self_ali_id}</Typography.Paragraph>
      <Typography.Paragraph>收件人：{conversation.customer.name} · 登录 ID：{conversation.customer.loginId || "缺失，请先补全联系人资料"}</Typography.Paragraph>
      <Typography.Paragraph>先搜索联系人，再返回工作台查看实际截图并二次确认，之后才会填入或发送。</Typography.Paragraph>
      {submission?.newIntent && <Alert type="warning" title="这是独立的新任务，即使文字相同也可能再次发送。请先核对旧任务与客户端实际结果。" />}
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words">{submission?.content}</pre>
      {outbox.error && <Alert type="warning" title={outbox.error} />}
    </Modal>
    <Modal title="确认重试" open={Boolean(retry)} onCancel={() => setRetry(undefined)} onOk={() => { if (retry) void outbox.mutate(retry, "retry"); setRetry(undefined); }} okText="重新搜索并截图" okButtonProps={{ disabled: !canOperate || !retry || !canRetry(outbox.tasks.find((task) => task.id === retry.id) ?? retry) }}>
      <Typography.Paragraph>仅对失败且未可能发送的任务重试。重试会重新搜索并截图，必须再次确认联系人后才会继续。</Typography.Paragraph>
      <pre className="whitespace-pre-wrap break-words">{retry?.content}</pre>
    </Modal>
    {review?.status === "awaiting_confirmation" && !blocked && <ScreenshotConfirmation
      key={`${review.id}:${review.version}:${review.screenshot_id}`}
      task={review} seller={snapshot!.account.self_ali_id} recipient={conversation.customer.name}
      canOperate={canOperate} backend={outbox.backend} onTask={(task) => outbox.accept([task], true)} onClose={() => setReviewId(undefined)}
    />}
  </>;
}
