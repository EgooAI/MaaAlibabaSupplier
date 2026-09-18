"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import { mergeMessageTextTranslations } from "@/domain/chat/chatModel";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { loadDraft, saveDraft } from "@/features/account/draftStorage";
import type { AssistantSuggestion, ChatMessage, ConversationDetail } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";
import type { ConversationQuery } from "@/types/inbox";
import { adaptInboxState } from "@/services/chatAdapter";

type AnalysisState = {
  loading: boolean;
  error?: string;
};

type TranslationJobState = {
  conversationId: string;
  taskId: string;
  messageIds: string[];
  texts: string[];
};

const TRANSLATION_POLL_INTERVAL_MS = 2000;
const TRANSLATION_POLL_MAX_TICKS = 150;
const TRANSLATION_QUERY_CHUNK = 500;

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
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [analysisState, setAnalysisState] = useState<AnalysisState>({ loading: false });
  const [translationVisible, setTranslationVisible] = useState(true);
  const [translationJob, setTranslationJob] = useState<TranslationJobState>();
  const [translationPendingIds, setTranslationPendingIds] = useState<ReadonlySet<string>>(() => new Set());
  const [translationFailedIds, setTranslationFailedIds] = useState<ReadonlySet<string>>(() => new Set());
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
    const resolvedIds = sourceMessages
      .filter((item) => item.role !== "card" && typeof translations[item.content] === "string" && translations[item.content]?.trim())
      .map((item) => item.id);
    const current = activeConversationRef.current;
    if (current && current.id === conversationId) {
      commitConversation({ ...current, messages: mergeMessageTextTranslations(current.messages, translations) });
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
    let translations: Record<string, string | null> = {};
    const chunks: string[][] = [];
    for (let index = 0; index < texts.length; index += TRANSLATION_QUERY_CHUNK) chunks.push(texts.slice(index, index + TRANSLATION_QUERY_CHUNK));
    // Partial failures only drop their own chunk; later refreshes retry.
    const settled = await Promise.allSettled(chunks.map((chunk) => backend.queryTranslations({ texts: chunk })));
    for (const result of settled) {
      if (result.status === "fulfilled") translations = { ...translations, ...result.value.translations };
    }
    if (activeIdRef.current !== conversationId) return;
    applyTextTranslations(conversationId, sourceMessages, translations);
  }, [applyTextTranslations, backend]);

  /** Pull cached translations for messages without one and merge them in. */
  const absorbTranslations = useCallback(async (conversationId: string, sourceMessages?: ChatMessage[]) => {
    const messages = sourceMessages ?? (activeConversationRef.current?.id === conversationId ? activeConversationRef.current.messages : undefined);
    if (!messages) return;
    const texts = [...new Set(messages.filter((item) => item.role !== "card" && !item.translatedContent && item.content.trim()).map((item) => item.content.trim()))];
    if (!texts.length) return;
    await queryAndApplyTranslations(conversationId, messages, texts);
  }, [queryAndApplyTranslations]);

  /** Re-read explicit texts after a job completes; force retranslations replace old values. */
  const absorbTranslationTexts = useCallback(async (conversationId: string, texts: string[]) => {
    if (!texts.length) return;
    const conversation = activeConversationRef.current;
    const messages = conversation?.id === conversationId ? conversation.messages : undefined;
    if (!messages) return;
    await queryAndApplyTranslations(conversationId, messages, texts);
  }, [queryAndApplyTranslations]);

  const finishTranslationJob = useCallback(async (job: TranslationJobState, outcome: "succeeded" | "failed" | "timeout" | "aborted") => {
    setTranslationJob((current) => current && current.taskId === job.taskId ? undefined : current);
    if (outcome === "aborted") return;
    await absorbTranslationTexts(job.conversationId, job.texts);
    if (activeIdRef.current !== job.conversationId) return;
    const conversation = activeConversationRef.current;
    const translatedNow = new Set(
      (conversation?.messages ?? [])
        .filter((item) => item.translatedContent)
        .map((item) => item.id),
    );
    const unresolved = job.messageIds.filter((id) => !translatedNow.has(id));
    setTranslationPendingIds(new Set());
    if (unresolved.length && outcome !== "timeout") {
      setTranslationFailedIds((current) => new Set([...current, ...unresolved]));
      if (outcome === "failed") message.error("翻译失败，可点击消息重试");
      else message.warning(`有 ${unresolved.length} 条消息未返回译文，可重试`);
    }
    if (outcome === "timeout") message.info("翻译仍在后台进行，稍后刷新会自动显示");
  }, [absorbTranslationTexts, message]);

  // Poll the active translation job; pauses while the tab is hidden.
  useEffect(() => {
    const job = translationJob;
    if (!job) return;
    let ticks = 0;
    let cancelled = false;
    const timer = setInterval(() => {
      if (cancelled || document.hidden) return;
      ticks += 1;
      if (ticks > TRANSLATION_POLL_MAX_TICKS) {
        void finishTranslationJob(job, "timeout");
        return;
      }
      void (async () => {
        try {
          const snapshot = await backend.getTranslationJob(job.taskId);
          if (cancelled) return;
          if (snapshot && (snapshot.status === "succeeded" || snapshot.status === "failed")) {
            void finishTranslationJob(job, snapshot.status);
          }
        } catch (error) {
          if (cancelled) return;
          if (error instanceof AccountChangedError) {
            void finishTranslationJob(job, "aborted");
            return;
          }
          // transient poll failure: keep polling until the tick cap
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
    const force = options?.force ?? false;
    // Any party's textual message is translatable; card bubbles carry no text.
    const eligible = targets.filter((item) => item.role !== "card" && item.content.trim());
    if (!eligible.length) {
      if (force) message.info("没有可重新翻译的消息");
      return;
    }
    const texts = [...new Set(eligible.map((item) => item.content.trim()))];
    try {
      const job = await backend.requestTranslations({ texts, force });
      if (activeIdRef.current !== conversationId) return;
      const ids = eligible.map((item) => item.id);
      setTranslationFailedIds((current) => {
        if (!current.size) return current;
        const next = new Set(current);
        for (const id of ids) next.delete(id);
        return next.size === current.size ? current : next;
      });
      if (!job.task_id) {
        void absorbTranslations(conversationId);
        return;
      }
      setTranslationPendingIds((current) => new Set([...current, ...ids]));
      setTranslationJob({ conversationId, taskId: job.task_id, messageIds: ids, texts });
    } catch (error) {
      if (!(error instanceof AccountChangedError)) message.error(force ? "重新翻译提交失败" : "翻译提交失败");
    }
  }, [absorbTranslations, backend, message]);

  const translatableMessages = useMemo(() => activeConversation?.messages.filter((item) => item.role !== "card" && item.content.trim()) ?? [], [activeConversation?.messages]);

  const translationStats = useMemo(() => {
    const translated = translatableMessages.filter((item) => item.translatedContent).length;
    return {
      total: translatableMessages.length,
      translated,
      untranslated: translatableMessages.length - translated,
      pending: translationPendingIds.size,
    };
  }, [translatableMessages, translationPendingIds]);

  const translateMissing = useCallback(() => {
    const targets = translatableMessages.filter((item) => !item.translatedContent);
    if (!targets.length) {
      message.info("译文已齐全");
      return;
    }
    void translateMessages(targets);
  }, [translatableMessages, message, translateMessages]);

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
    setTranslationPendingIds(new Set());
    setTranslationFailedIds(new Set());
    try {
      const saved = loadDraft(() => localStorage, account, id, warnStorage);
      setDrafts((current) => ({ ...current, [id]: current[id] ?? saved }));
    } catch { warnStorage(); }
    commitConversation(undefined);
    setActiveCardId(undefined);
    setAnalysisState({ loading: false });
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
      const merged: ConversationDetail = current && current.id === detail.id
        ? {
            ...detail,
            analysis: current.analysis ?? detail.analysis,
            messages: detail.messages.map((item) => {
              const old = current.messages.find((candidate) => candidate.id === item.id);
              return old?.content === item.content && old.translatedContent ? { ...item, translatedContent: old.translatedContent } : item;
            }),
          }
        : detail;
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

    try {
      const nextSuggestions = await backend.getAssistantSuggestions(conversationId);
      if (suggestionRequestRef.current === requestId && activeIdRef.current === conversationId) setSuggestions(nextSuggestions);
    } catch {
      if (suggestionRequestRef.current === requestId && activeIdRef.current === conversationId) message.error("回复建议加载失败");
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
    } catch {
      if (analysisRequestRef.current === requestId && activeIdRef.current === conversationId) {
        setAnalysisState({ loading: false, error: "会话分析失败" });
        message.error("会话分析失败");
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
    gotoContact,
    suggestions,
    suggestionOpen,
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
