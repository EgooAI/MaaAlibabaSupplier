"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { isResolvedTranslation, isTranslatableMessage, mergeMessageTextTranslations } from "@/domain/chat/chatModel";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import type { ChatMessage, ConversationDetail } from "@/types/chatCanonical";
import { operationErrorMessage } from "@/services/errors";

type TranslationJobState = {
  conversationId: string;
  taskIds: string[];
  selection: number;
  deadline: number;
  messageIds: string[];
  texts: string[];
};

const TRANSLATION_POLL_INTERVAL_MS = 2000;
const TRANSLATION_OBSERVATION_MS = 300000;
const TRANSLATION_QUERY_CHUNK = 500;
const TRANSLATION_SUBMIT_CHUNK = 500;

/** Message translation: submit, observe, absorb cache and expose pending state. */
export function useConversationTranslation(
  activeConversation: ConversationDetail | undefined,
  activeIdRef: RefObject<string | undefined>,
  activeConversationRef: RefObject<ConversationDetail | undefined>,
  selectionRef: RefObject<number>,
  commitConversation: (next: ConversationDetail | undefined) => void,
) {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const [translationVisible, setTranslationVisible] = useState(true);
  const [translationJob, setTranslationJob] = useState<TranslationJobState>();
  const [translationNotice, setTranslationNotice] = useState<string>();
  const translationBusy = useRef(false);
  const [translationPendingIds, setTranslationPendingIds] = useState<ReadonlySet<string>>(() => new Set());
  const [translationFailedIds, setTranslationFailedIds] = useState<ReadonlySet<string>>(() => new Set());
  // NO_NEED 哨兵（空串译文）已解决但不可渲染：以文本为键。ref 是同步事实源
  // （异步续体不得读 pre-commit 状态），state 仅镜像供渲染期统计使用。
  const noNeedTextsRef = useRef<ReadonlySet<string>>(new Set());
  const [translationNoNeedTexts, setTranslationNoNeedTexts] = useState<ReadonlySet<string>>(() => new Set());

  const applyTextTranslations = useCallback((conversationId: string, sourceMessages: ChatMessage[], translations: Record<string, string | null>) => {
    const current = activeConversationRef.current;
    if (current && current.id === conversationId) {
      commitConversation({ ...current, messages: mergeMessageTextTranslations(current.messages, translations) });
    }
    const resolvedIds = sourceMessages
      .filter((item) => isTranslatableMessage(item) && isResolvedTranslation(translations[item.content.trim()]))
      .map((item) => item.id);
    let nextNoNeed: Set<string> | undefined;
    for (const [text, value] of Object.entries(translations)) {
      if (!isResolvedTranslation(value)) continue;
      const noNeed = value.trim().length === 0;
      if (noNeed === noNeedTextsRef.current.has(text)) continue;
      nextNoNeed ??= new Set(noNeedTextsRef.current);
      if (noNeed) nextNoNeed.add(text);
      else nextNoNeed.delete(text);
    }
    if (nextNoNeed) {
      noNeedTextsRef.current = nextNoNeed;
      setTranslationNoNeedTexts(nextNoNeed);
    }
    if (!resolvedIds.length) return;
    setTranslationPendingIds((current2) => {
      const next = new Set(current2);
      for (const id of resolvedIds) next.delete(id);
      return next.size === current2.size ? current2 : next;
    });
    setTranslationFailedIds((current2) => {
      const next = new Set(current2);
      for (const id of resolvedIds) next.delete(id);
      return next.size === current2.size ? current2 : next;
    });
  }, [activeConversationRef, commitConversation]);

  /** Fetch cached translations for explicit texts and merge them into the conversation. */
  const queryAndApplyTranslations = useCallback(async (conversationId: string, sourceMessages: ChatMessage[], texts: string[]) => {
    const selection = selectionRef.current;
    let translations: Record<string, string | null> = {};
    const chunks: string[][] = [];
    for (let index = 0; index < texts.length; index += TRANSLATION_QUERY_CHUNK) chunks.push(texts.slice(index, index + TRANSLATION_QUERY_CHUNK));
    // Partial failures only drop their own chunk; later refreshes retry.
    const settled = await Promise.allSettled(chunks.map((chunk) => backend.queryTranslations({ texts: chunk })));
    for (const result of settled) {
      if (result.status === "fulfilled") translations = { ...translations, ...result.value.translations };
    }
    if (activeIdRef.current !== conversationId || selection !== selectionRef.current) return false;
    applyTextTranslations(conversationId, sourceMessages, translations);
    const complete = settled.every((result) => result.status === "fulfilled");
    if (!complete) setTranslationNotice("译文缓存查询失败，无法确认翻译结果；请稍后查询缓存，勿立即重复提交。");
    return complete;
  }, [activeIdRef, applyTextTranslations, backend, selectionRef]);

  /** Pull cached translations for messages without one and merge them in. */
  const absorbTranslations = useCallback(async (conversationId: string, sourceMessages?: ChatMessage[]) => {
    const messages = sourceMessages ?? (activeConversationRef.current?.id === conversationId ? activeConversationRef.current.messages : undefined);
    if (!messages) return;
    const texts = [...new Set(messages
      .filter((item) => isTranslatableMessage(item) && !item.translatedContent && !noNeedTextsRef.current.has(item.content.trim()))
      .map((item) => item.content.trim()))];
    if (!texts.length) return;
    return queryAndApplyTranslations(conversationId, messages, texts);
  }, [activeConversationRef, queryAndApplyTranslations]);

  /** Re-read explicit texts after a job completes; force retranslations replace old values. */
  const absorbTranslationTexts = useCallback(async (conversationId: string, texts: string[]) => {
    if (!texts.length) return;
    const conversation = activeConversationRef.current;
    const messages = conversation?.id === conversationId ? conversation.messages : undefined;
    if (!messages) return;
    return queryAndApplyTranslations(conversationId, messages, texts);
  }, [activeConversationRef, queryAndApplyTranslations]);

  const finishTranslationJob = useCallback(async (job: TranslationJobState, outcome: "succeeded" | "failed" | "unknown" | "aborted") => {
    if (selectionRef.current !== job.selection) return;
    setTranslationJob((current) => current === job ? undefined : current);
    if (outcome === "aborted") {
      translationBusy.current = false;
      setTranslationPendingIds(new Set());
      setTranslationNotice("账号状态已变化，已停止观察原翻译任务；其结果尚未确认。");
      return;
    }
    const cacheObserved = await absorbTranslationTexts(job.conversationId, job.texts);
    if (activeIdRef.current !== job.conversationId || selectionRef.current !== job.selection) return;
    translationBusy.current = false;
    setTranslationPendingIds(new Set());
    if (outcome === "unknown") setTranslationNotice("翻译结果未知：任务记录缺失或观察已到期。请查询缓存核对，勿立即重复提交。");
    if (!cacheObserved) return;
    // 详情尚未就绪（快速重选竞态）时跳过未解决判定，避免把成功任务误标为失败。
    const conversation = activeConversationRef.current;
    if (!conversation) return;
    const resolvedNow = new Set(
      conversation.messages
        .filter((item) => item.translatedContent || (isTranslatableMessage(item) && noNeedTextsRef.current.has(item.content.trim())))
        .map((item) => item.id),
    );
    const unresolved = job.messageIds.filter((id) => !resolvedNow.has(id));
    if (unresolved.length && outcome !== "unknown") {
      setTranslationFailedIds((current) => new Set([...current, ...unresolved]));
      if (outcome === "failed") message.error("翻译失败，可点击消息重试");
      else message.warning(`有 ${unresolved.length} 条消息未返回译文，可重试`);
    }
  }, [absorbTranslationTexts, activeConversationRef, activeIdRef, message, selectionRef]);

  // Poll the active translation job; pauses while the tab is hidden.
  useEffect(() => {
    const job = translationJob;
    if (!job) return;
    let cancelled = false;
    let busy = false;
    let finished = false;
    const pending = new Set(job.taskIds);
    let outcome: "succeeded" | "failed" | "unknown" = "succeeded";
    const timer = setInterval(() => {
      if (cancelled || finished || busy || document.hidden) return;
      if (Date.now() >= job.deadline) {
        finished = true;
        void finishTranslationJob(job, "unknown");
        return;
      }
      busy = true;
      void (async () => {
        try {
          for (const taskId of pending) {
            const snapshot = await backend.getTranslationJob(taskId);
            if (cancelled) return;
            if (!snapshot || snapshot.status === "succeeded" || snapshot.status === "failed") {
              pending.delete(taskId);
              if (!snapshot) outcome = "unknown";
              else if (snapshot.status === "failed" && outcome !== "unknown") outcome = "failed";
            }
          }
          if (!pending.size) {
            finished = true;
            void finishTranslationJob(job, outcome);
          }
        } catch (error) {
          if (cancelled) return;
          if (error instanceof AccountChangedError) {
            finished = true;
            void finishTranslationJob(job, "aborted");
            return;
          }
          setTranslationNotice(operationErrorMessage(error, "翻译任务观察失败") + "；将继续查询状态，不会自动重新提交。");
        } finally {
          busy = false;
        }
      })();
    }, TRANSLATION_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [translationJob, backend, finishTranslationJob]);

  const translateMessages = useCallback(async (targets: ChatMessage[], options?: { force?: boolean }) => {
    const conversationId = activeIdRef.current;
    if (!conversationId) return;
    if (translationBusy.current) { message.info("翻译已提交，请等待观察结果"); return; }
    const selection = selectionRef.current;
    const force = options?.force ?? false;
    // Any party's textual message is translatable; card bubbles carry no text.
    const eligible = targets.filter(isTranslatableMessage);
    if (!eligible.length) {
      if (force) message.info("没有可重新翻译的消息");
      return;
    }
    const texts = [...new Set(eligible.map((item) => item.content.trim()))];
    // 传会话 ID 让后端把全量历史作为翻译上下文；非数字 ID（mock）则省略。
    const numericId = Number(conversationId);
    // Track every accepted batch; a later rejected batch cannot erase earlier receipts.
    const batches: string[][] = [];
    for (let index = 0; index < texts.length; index += TRANSLATION_SUBMIT_CHUNK) batches.push(texts.slice(index, index + TRANSLATION_SUBMIT_CHUNK));
    const taskIds: string[] = [];
    const acceptedTexts: string[] = [];
    translationBusy.current = true;
    setTranslationNotice(undefined);
    try {
      for (const batch of batches) {
        if (selectionRef.current !== selection) return;
        const job = await backend.requestTranslations({
          texts: batch,
          force,
          ...(Number.isFinite(numericId) ? { conversationId: numericId } : {}),
        });
        if (selectionRef.current !== selection) return;
        acceptedTexts.push(...batch);
        if (job.task_id) taskIds.push(job.task_id);
      }
    } catch (error) {
      if (error instanceof AccountChangedError) {
        if (selectionRef.current === selection) translationBusy.current = false;
        return;
      }
      if (selectionRef.current !== selection) return;
      setTranslationNotice(operationErrorMessage(error, "翻译提交失败", "请先查询缓存核对，勿立即重复提交"));
    }
    if (activeIdRef.current !== conversationId || selectionRef.current !== selection) return;
    const accepted = new Set(acceptedTexts);
    const ids = eligible.filter((item) => accepted.has(item.content.trim())).map((item) => item.id);
    setTranslationFailedIds((current) => {
      if (!current.size) return current;
      const next = new Set(current);
      for (const id of ids) next.delete(id);
      return next.size === current.size ? current : next;
    });
    if (!taskIds.length) {
      translationBusy.current = false;
      void absorbTranslations(conversationId);
      return;
    }
    setTranslationPendingIds((current) => new Set([...current, ...ids]));
    setTranslationJob({
      conversationId, taskIds, messageIds: ids, texts: acceptedTexts, selection,
      deadline: Date.now() + TRANSLATION_OBSERVATION_MS,
    });
  }, [absorbTranslations, activeIdRef, backend, message, selectionRef]);

  const translatableMessages = useMemo(() => activeConversation?.messages.filter(isTranslatableMessage) ?? [], [activeConversation?.messages]);

  const translationStats = useMemo(() => {
    const noNeed = translatableMessages.filter((item) => !item.translatedContent && translationNoNeedTexts.has(item.content.trim())).length;
    const translated = translatableMessages.filter((item) => item.translatedContent).length;
    return {
      total: translatableMessages.length,
      translated,
      untranslated: translatableMessages.length - translated - noNeed,
      pending: translationPendingIds.size,
    };
  }, [translatableMessages, translationPendingIds, translationNoNeedTexts]);

  const translateMissing = useCallback(() => {
    const targets = translatableMessages.filter((item) => !item.translatedContent && !translationNoNeedTexts.has(item.content.trim()));
    if (!targets.length) {
      message.info("译文已齐全");
      return;
    }
    void translateMessages(targets);
  }, [translatableMessages, translationNoNeedTexts, message, translateMessages]);

  const retranslateConversation = useCallback(() => {
    void translateMessages(translatableMessages, { force: true });
  }, [translatableMessages, translateMessages]);

  const toggleTranslation = useCallback(() => {
    setTranslationVisible((current) => !current);
  }, []);

  /** Drop conversation-scoped observation state and pending marks. */
  const reset = useCallback(() => {
    setTranslationJob(undefined);
    translationBusy.current = false;
    setTranslationNotice(undefined);
    setTranslationPendingIds(new Set());
    setTranslationFailedIds(new Set());
    noNeedTextsRef.current = new Set();
    setTranslationNoNeedTexts(new Set());
  }, []);

  const reconcileTranslations = useCallback(async () => {
    const id = activeIdRef.current;
    const selection = selectionRef.current;
    if (!id) return;
    const texts = activeConversationRef.current?.messages.filter(isTranslatableMessage).map((item) => item.content.trim()) ?? [];
    const observed = await absorbTranslationTexts(id, texts);
    if (selectionRef.current === selection && observed) {
      setTranslationNotice("缓存查询完成；未返回译文的消息仍无法确认任务结果。重新提交可能重复调用模型，请人工核对。");
    }
  }, [absorbTranslationTexts, activeConversationRef, activeIdRef, selectionRef]);

  return {
    absorbTranslations,
    reset,
    translateMessages,
    translateMissing,
    retranslateConversation,
    translationVisible,
    toggleTranslation,
    translationStats,
    translationPendingIds,
    translationFailedIds,
    translationNotice,
    reconcileTranslations,
  };
}
