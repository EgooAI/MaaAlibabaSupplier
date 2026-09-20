"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import { isResolvedTranslation, isTranslatableMessage, mergeConversationDetail, mergeMessageTextTranslations } from "@/domain/chat/chatModel";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { loadDraft, saveDraft } from "@/features/account/draftStorage";
import type { AssistantSuggestion, ChatMessage, ConversationDetail } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";
import type { ConversationQuery } from "@/types/inbox";
import { adaptInboxState } from "@/services/chatAdapter";
import { operationErrorMessage } from "@/services/errors";

type AnalysisState = {
  loading: boolean;
  error?: string;
};

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

export function useChatWorkbench(initialQuery: ConversationQuery = {}) {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const { snapshot, refreshReads } = useAccount();
  const account = snapshot!.account;
  const draftStorageFailed = useRef(false);
  const warnStorage = useCallback(() => {
    if (draftStorageFailed.current) return;
    draftStorageFailed.current = true;
    message.warning("草稿暂存于当前标签页内存，切换账号后仍保留，存储恢复后会补存；关闭或刷新浏览器前请妥善保留内容");
  }, [message]);
  const summaries = useConversationSummaries(initialQuery);
  const { conversations, loading, detailRevision, refreshError, refreshPending, beginRead } = summaries;
  const [markingRead, setMarkingRead] = useState(false);
  const readBusy = useRef(false);
  const [activeConversationId, setActiveConversationId] = useState<string>();
  const [activeConversation, setActiveConversation] = useState<ConversationDetail>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [suggestions, setSuggestions] = useState<AssistantSuggestion[]>([]);
  const [suggestionOpen, setSuggestionOpen] = useState(false);
  const [suggestionError, setSuggestionError] = useState<string>();
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [analysisState, setAnalysisState] = useState<AnalysisState>({ loading: false });
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
  const [groupMode, setGroupMode] = useState<ConversationGroupMode>("time");
  const [activeCardId, setActiveCardId] = useState<string>();
  // 并发守卫：快速切换会话时丢弃旧请求回包，各请求域独立计数。
  const activeIdRef = useRef<string | undefined>(undefined);
  const activeConversationRef = useRef<ConversationDetail | undefined>(undefined);
  const selectionRef = useRef(0);
  const detailRequestRef = useRef(0);
  const suggestionRequestRef = useRef(0);
  const analysisRequestRef = useRef(0);

  useEffect(() => () => {
    activeIdRef.current = undefined;
    activeConversationRef.current = undefined;
    ++selectionRef.current;
    ++detailRequestRef.current;
    ++suggestionRequestRef.current;
    ++analysisRequestRef.current;
  }, []);

  // The ref is the synchronous source of truth so async continuations (job
  // polling, cache absorbs) never read a pre-commit snapshot; state mirrors it.
  const commitConversation = useCallback((next: ConversationDetail | undefined) => {
    activeConversationRef.current = next;
    setActiveConversation(next);
  }, []);

  const draft = activeConversationId ? drafts[activeConversationId] ?? "" : "";

  const setDraft = useCallback((value: string) => {
    const id = activeIdRef.current;
    if (!id) return;
    setDrafts((current) => ({ ...current, [id]: value }));
    try { saveDraft(() => localStorage, account, id, value); } catch { warnStorage(); }
  }, [account, warnStorage]);

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
  }, [commitConversation]);

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
  }, [applyTextTranslations, backend]);

  /** Pull cached translations for messages without one and merge them in. */
  const absorbTranslations = useCallback(async (conversationId: string, sourceMessages?: ChatMessage[]) => {
    const messages = sourceMessages ?? (activeConversationRef.current?.id === conversationId ? activeConversationRef.current.messages : undefined);
    if (!messages) return;
    const texts = [...new Set(messages
      .filter((item) => isTranslatableMessage(item) && !item.translatedContent && !noNeedTextsRef.current.has(item.content.trim()))
      .map((item) => item.content.trim()))];
    if (!texts.length) return;
    return queryAndApplyTranslations(conversationId, messages, texts);
  }, [queryAndApplyTranslations]);

  /** Re-read explicit texts after a job completes; force retranslations replace old values. */
  const absorbTranslationTexts = useCallback(async (conversationId: string, texts: string[]) => {
    if (!texts.length) return;
    const conversation = activeConversationRef.current;
    const messages = conversation?.id === conversationId ? conversation.messages : undefined;
    if (!messages) return;
    return queryAndApplyTranslations(conversationId, messages, texts);
  }, [queryAndApplyTranslations]);

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
  }, [absorbTranslationTexts, message]);

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
    setTranslationJob({ conversationId, taskIds, messageIds: ids, texts: acceptedTexts, selection, deadline: Date.now() + TRANSLATION_OBSERVATION_MS });
  }, [absorbTranslations, backend, message]);

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

  const selectConversation = useCallback(async (id: string) => {
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    activeIdRef.current = id;
    ++selectionRef.current;
    ++suggestionRequestRef.current;
    ++analysisRequestRef.current;
    setActiveConversationId(id);
    setTranslationJob(undefined);
    translationBusy.current = false;
    setTranslationNotice(undefined);
    setTranslationPendingIds(new Set());
    setTranslationFailedIds(new Set());
    noNeedTextsRef.current = new Set();
    setTranslationNoNeedTexts(new Set());
    try {
      const saved = loadDraft(() => localStorage, account, id, warnStorage);
      setDrafts((current) => ({ ...current, [id]: current[id] ?? saved }));
    } catch { warnStorage(); }
    commitConversation(undefined);
    setActiveCardId(undefined);
    setAnalysisState({ loading: false });
    setSuggestionError(undefined);
    setSuggestions([]);
    setDetailLoading(true);
    const acknowledge = beginRead("detail");

    try {
      const detail = await backend.getConversation(id);
      if (detailRequestRef.current !== requestId || activeIdRef.current !== id) return;
      commitConversation(detail);
      acknowledge(true, detail.isOverdue ? null : detail.dueAt);
      void absorbTranslations(id, detail.messages);
    } catch {
      acknowledge(false);
      if (detailRequestRef.current === requestId && activeIdRef.current === id) {
        message.error("会话详情加载失败");
      }
    } finally {
      if (detailRequestRef.current === requestId && activeIdRef.current === id) {
        setDetailLoading(false);
      }
    }
  }, [account, absorbTranslations, backend, commitConversation, message, warnStorage, beginRead]);

  useEffect(() => {
    if (!activeConversationId && conversations[0]) {
      queueMicrotask(() => selectConversation(conversations[0].id));
    }
  }, [activeConversationId, conversations, selectConversation]);

  // Keep the current detail visible while reading a committed version or retrying.
  const refreshActiveDetail = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    const acknowledge = beginRead("detail");
    try {
      const detail = await backend.getConversation(id);
      if (detailRequestRef.current !== requestId || activeIdRef.current !== id) return;
      const current = activeConversationRef.current;
      const merged: ConversationDetail = current && current.id === detail.id ? mergeConversationDetail(current, detail) : detail;
      commitConversation(merged);
      acknowledge(true, detail.isOverdue ? null : detail.dueAt);
      void absorbTranslations(id, merged.messages);
    } catch {
      acknowledge(false);
    } finally {
      if (detailRequestRef.current === requestId && activeIdRef.current === id) setDetailLoading(false);
    }
  }, [absorbTranslations, backend, beginRead, commitConversation]);

  const revisionSeenRef = useRef(false);
  useEffect(() => {
    if (!revisionSeenRef.current) {
      revisionSeenRef.current = true;
      return;
    }
    void refreshActiveDetail();
  }, [detailRevision, refreshActiveDetail]);

  const openSuggestions = useCallback(async () => {
    const conversationId = activeConversation?.id;
    if (!conversationId) return;
    const requestId = suggestionRequestRef.current + 1;
    suggestionRequestRef.current = requestId;
    setSuggestionOpen(true);
    setSuggestions([]);
    setSuggestionError(undefined);

    try {
      const nextSuggestions = await backend.getAssistantSuggestions(conversationId);
      if (suggestionRequestRef.current === requestId && activeIdRef.current === conversationId) setSuggestions(nextSuggestions);
    } catch (error) {
      if (suggestionRequestRef.current === requestId && activeIdRef.current === conversationId) {
        const text = operationErrorMessage(error, "回复建议加载失败", "本次建议可能仍在生成，丢失的结果无法重新读取；稍后人工决定是否重新生成，可能重复调用模型");
        setSuggestionError(text);
        message.error(text);
      }
    }
  }, [activeConversation?.id, backend, message]);

  const insertSuggestion = useCallback((content: string) => {
    setDraft(content);
    setSuggestionOpen(false);
    message.success("已插入到回复框");
  }, [message, setDraft]);

  const gotoContact = useCallback(async () => {
    const conversation = activeConversation;
    const loginId = conversation?.customer.loginId || conversation?.customer.name;
    if (!conversation || !loginId) {
      message.warning("缺少登录 ID，无法跳转");
      return;
    }
    try {
      await backend.gotoContact(conversation.id, loginId);
      message.success("已提交跳转任务");
    } catch (error) {
      if (!(error instanceof AccountChangedError)) message.error("跳转任务提交失败");
    }
  }, [activeConversation, backend, message]);

  const analyzeConversation = useCallback(async () => {
    const conversationId = activeConversation?.id;
    if (!conversationId) return;
    const requestId = analysisRequestRef.current + 1;
    analysisRequestRef.current = requestId;
    setAnalysisState({ loading: true });

    try {
      const analysis = await backend.analyzeConversation(conversationId);
      if (analysisRequestRef.current !== requestId || activeIdRef.current !== conversationId) return;
      const current = activeConversationRef.current;
      if (current?.id === conversationId) commitConversation({ ...current, analysis });
      setAnalysisState({ loading: false });
    } catch (error) {
      if (analysisRequestRef.current === requestId && activeIdRef.current === conversationId) {
        const text = operationErrorMessage(error, "会话分析失败", "本次分析可能仍在执行，丢失的结果无法重新读取；稍后人工决定是否重新生成，可能重复调用模型");
        setAnalysisState({ loading: false, error: text });
        message.error(text);
      }
    }
  }, [activeConversation?.id, backend, commitConversation, message]);

  const activeCard = useMemo(() => activeConversation?.messages.find((item) => item.card?.id === activeCardId)?.card, [activeCardId, activeConversation]);

  async function markRead() {
    const displayed = activeConversation;
    if (!displayed?.readSnapshot || readBusy.current) return;
    const selection = selectionRef.current;
    readBusy.current = true;
    setMarkingRead(true);
    // Capture the token belonging to the messages displayed at the click.
    const token = displayed.readSnapshot;
    try {
      const receipt = await backend.markConversationRead(displayed.id, token);
      if (activeIdRef.current === displayed.id && selection === selectionRef.current) {
        ++detailRequestRef.current;
        const current = activeConversationRef.current;
        if (current?.id === displayed.id) commitConversation({ ...current, ...adaptInboxState(receipt.state) });
      }
      refreshReads();
    } catch (error) {
      if (!(error instanceof AccountChangedError)) message.error("标记工作台已读失败，请重试");
    } finally {
      readBusy.current = false;
      setMarkingRead(false);
    }
  }

  return {
    ...summaries,
    markingRead,
    markRead,
    conversations,
    activeConversation,
    loading,
    refreshError,
    refreshPending,
    detailLoading,
    draft,
    setDraft,
    selectConversation,
    translateMessages,
    translateMissing,
    retranslateConversation,
    translationVisible,
    toggleTranslation,
    translationStats,
    translationPendingIds,
    translationFailedIds,
    translationNotice,
    reconcileTranslations: async () => {
      const id = activeIdRef.current;
      const selection = selectionRef.current;
      if (!id) return;
      const texts = activeConversationRef.current?.messages.filter(isTranslatableMessage).map((item) => item.content.trim()) ?? [];
      const observed = await absorbTranslationTexts(id, texts);
      if (selectionRef.current === selection && observed) setTranslationNotice("缓存查询完成；未返回译文的消息仍无法确认任务结果。重新提交可能重复调用模型，请人工核对。");
    },
    gotoContact,
    suggestions,
    suggestionOpen,
    suggestionError,
    setSuggestionOpen,
    openSuggestions,
    insertSuggestion,
    analysisOpen,
    setAnalysisOpen,
    analysisLoading: analysisState.loading,
    analysisError: analysisState.error,
    analyzeConversation,
    groupMode,
    setGroupMode,
    activeCard,
    setActiveCardId,
  };
}
