"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import { mergeConversationDetail } from "@/domain/chat/chatModel";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { loadDraft, saveDraft } from "@/features/account/draftStorage";
import type { AssistantSuggestion, ConversationDetail } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";
import { useConversationTranslation } from "./useConversationTranslation";
import type { ConversationQuery } from "@/types/inbox";
import { adaptInboxState } from "@/services/chatAdapter";
import { operationErrorMessage } from "@/services/errors";

type AnalysisState = {
  loading: boolean;
  error?: string;
};

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
  const { conversations, detailRevision, beginRead } = summaries;
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

  const translation = useConversationTranslation(
    activeConversation, activeIdRef, activeConversationRef, selectionRef, commitConversation,
  );
  const { absorbTranslations, reset: resetTranslation } = translation;

  const draft = activeConversationId ? drafts[activeConversationId] ?? "" : "";

  const setDraft = useCallback((value: string) => {
    const id = activeIdRef.current;
    if (!id) return;
    setDrafts((current) => ({ ...current, [id]: value }));
    try { saveDraft(() => localStorage, account, id, value); } catch { warnStorage(); }
  }, [account, warnStorage]);

  const selectConversation = useCallback(async (id: string) => {
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    activeIdRef.current = id;
    ++selectionRef.current;
    ++suggestionRequestRef.current;
    ++analysisRequestRef.current;
    setActiveConversationId(id);
    resetTranslation();
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
  }, [account, absorbTranslations, backend, commitConversation, message, warnStorage, beginRead, resetTranslation]);

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
    activeConversation,
    detailLoading,
    draft,
    setDraft,
    selectConversation,
    translateMessages: translation.translateMessages,
    translateMissing: translation.translateMissing,
    retranslateConversation: translation.retranslateConversation,
    translationVisible: translation.translationVisible,
    toggleTranslation: translation.toggleTranslation,
    translationStats: translation.translationStats,
    translationPendingIds: translation.translationPendingIds,
    translationFailedIds: translation.translationFailedIds,
    translationNotice: translation.translationNotice,
    reconcileTranslations: translation.reconcileTranslations,
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
