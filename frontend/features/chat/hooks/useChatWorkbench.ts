"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { mergeMessageTranslations, messageExecutionState } from "@/domain/chat/chatModel";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { loadDraft, saveDraft } from "@/features/account/draftStorage";
import type { AssistantSuggestion, ChatMessage, ConversationDetail } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";

type AnalysisState = {
  loading: boolean;
  error?: string;
};

export function useChatWorkbench() {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const { snapshot } = useAccount();
  const account = snapshot!.account;
  const draftStorageFailed = useRef(false);
  const warnStorage = useCallback(() => {
    if (draftStorageFailed.current) return;
    draftStorageFailed.current = true;
    message.warning("草稿暂存于当前标签页内存，切换账号后仍保留，存储恢复后会补存；关闭或刷新浏览器前请妥善保留内容");
  }, [message]);
  const { conversations, loading, revision } = useConversationSummaries();
  const [activeConversationId, setActiveConversationId] = useState<string>();
  const [activeConversation, setActiveConversation] = useState<ConversationDetail>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [sendingConversationId, setSendingConversationId] = useState<string>();
  const [suggestions, setSuggestions] = useState<AssistantSuggestion[]>([]);
  const [suggestionOpen, setSuggestionOpen] = useState(false);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [analysisState, setAnalysisState] = useState<AnalysisState>({ loading: false });
  const [translationVisible, setTranslationVisible] = useState(true);
  const [groupMode, setGroupMode] = useState<ConversationGroupMode>("time");
  const [activeCardId, setActiveCardId] = useState<string>();
  // 并发守卫：快速切换会话时丢弃旧请求回包，各请求域独立计数。
  const activeIdRef = useRef<string | undefined>(undefined);
  const detailRequestRef = useRef(0);
  const sendRequestRef = useRef(0);
  const translateRequestRef = useRef(0);
  const suggestionRequestRef = useRef(0);
  const analysisRequestRef = useRef(0);
  const translationVisibleRef = useRef(true);

  useEffect(() => () => {
    activeIdRef.current = undefined;
    ++detailRequestRef.current;
    ++sendRequestRef.current;
    ++translateRequestRef.current;
    ++suggestionRequestRef.current;
    ++analysisRequestRef.current;
  }, []);

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
    setActiveConversationId(id);
    try {
      const saved = loadDraft(() => localStorage, account, id, warnStorage);
      setDrafts((current) => ({ ...current, [id]: current[id] ?? saved }));
    } catch { warnStorage(); }
    setActiveConversation(undefined);
    setActiveCardId(undefined);
    setAnalysisState({ loading: false });
    setDetailLoading(true);

    try {
      const detail = await backend.getConversation(id);
      if (detailRequestRef.current !== requestId || activeIdRef.current !== id) return;
      setActiveConversation(detail);
    } catch {
      if (detailRequestRef.current === requestId && activeIdRef.current === id) {
        message.error("会话详情加载失败");
      }
    } finally {
      if (detailRequestRef.current === requestId && activeIdRef.current === id) {
        setDetailLoading(false);
      }
    }
  }, [account, backend, message, warnStorage]);

  useEffect(() => {
    if (!activeConversationId && conversations[0]) {
      queueMicrotask(() => selectConversation(conversations[0].id));
    }
  }, [activeConversationId, conversations, selectConversation]);

  // Quiet detail refresh when the source revision moves (list already reloaded
  // by useConversationSummaries). No spinner clearing: the old messages stay
  // visible until the fresh detail swaps in. Failures stay silent to avoid
  // toast storms from the 10s poll.
  const refreshActiveDetail = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) return;
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    try {
      const detail = await backend.getConversation(id);
      if (detailRequestRef.current !== requestId || activeIdRef.current !== id) return;
      setActiveConversation(detail);
    } catch {
      // Silent on purpose (see above).
    }
  }, [backend]);

  const revisionSeenRef = useRef(false);
  useEffect(() => {
    if (!revisionSeenRef.current) {
      revisionSeenRef.current = true;
      return;
    }
    void refreshActiveDetail();
  }, [revision, refreshActiveDetail]);

  const translate = useCallback(async (messageItem: ChatMessage, regenerate = false) => {
    const conversationId = activeConversation?.id;
    if (!conversationId) return;
    const requestId = translateRequestRef.current + 1;
    translateRequestRef.current = requestId;
    translationVisibleRef.current = true;

    try {
      const result = regenerate
        ? await backend.regenerateTranslation({ conversationId, messageId: messageItem.id, targetLanguage: "zh-CN" })
        : await backend.translateMessage({ conversationId, messageId: messageItem.id, targetLanguage: "zh-CN" });
      if (translateRequestRef.current !== requestId || activeIdRef.current !== conversationId) return;

      setActiveConversation((current) => {
        if (!current || current.id !== conversationId) return current;
        return { ...current, messages: mergeMessageTranslations(current.messages, [result]) };
      });
      if (translationVisibleRef.current) setTranslationVisible(true);
      message.success(regenerate ? "已重新翻译" : "已翻译消息");
    } catch {
      if (translateRequestRef.current === requestId && activeIdRef.current === conversationId) message.error(regenerate ? "重新翻译失败" : "消息翻译失败");
    }
  }, [activeConversation?.id, backend, message]);

  const translateConversation = useCallback(async (options?: { force?: boolean }) => {
    const force = options?.force ?? false;
    const conversationId = activeConversation?.id;
    const messages = activeConversation?.messages ?? [];
    if (!conversationId) return;
    const requestId = translateRequestRef.current + 1;
    translateRequestRef.current = requestId;
    translationVisibleRef.current = true;

    const buyerMessages = force
      ? messages.filter((item) => item.role === "buyer")
      : messages.filter((item) => item.role === "buyer" && !item.translatedContent);
    if (!buyerMessages.length) {
      setTranslationVisible(true);
      return;
    }

    try {
      if (force) {
        const texts = buyerMessages.map((item) => item.content).filter((text) => text.trim());
        if (texts.length) await backend.requestTranslations({ texts, force: true });
      }
      const translatedMessages = await Promise.all(
        buyerMessages.map((item) => force
          ? backend.regenerateTranslation({ conversationId, messageId: item.id, targetLanguage: "zh-CN" })
          : backend.translateMessage({ conversationId, messageId: item.id, targetLanguage: "zh-CN" })),
      );
      if (translateRequestRef.current !== requestId || activeIdRef.current !== conversationId) return;
      setActiveConversation((current) => {
        if (!current || current.id !== conversationId) return current;
        return { ...current, messages: mergeMessageTranslations(current.messages, translatedMessages) };
      });
      if (translationVisibleRef.current) setTranslationVisible(true);
      message.success(force ? "已重新翻译当前会话" : "已翻译当前会话");
    } catch {
      if (translateRequestRef.current === requestId && activeIdRef.current === conversationId) message.error(force ? "重新翻译失败" : "会话翻译失败");
    }
  }, [activeConversation?.id, activeConversation?.messages, backend, message]);

  const retranslateConversation = useCallback(() => translateConversation({ force: true }), [translateConversation]);

  const toggleTranslation = useCallback(() => {
    if (translationVisible) {
      translationVisibleRef.current = false;
      setTranslationVisible(false);
      return;
    }
    void translateConversation();
  }, [translationVisible, translateConversation]);

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

  const sendMessage = useCallback(async (action: "send" | "test" = "send") => {
    const conversationId = activeConversation?.id;
    const submittedDraft = draft.trim();
    if (!conversationId || !submittedDraft || sendingConversationId === conversationId) return;
    const requestId = sendRequestRef.current + 1;
    sendRequestRef.current = requestId;

    setSendingConversationId(conversationId);
    try {
      const result = await backend.sendMessage({ conversationId, content: submittedDraft, action });
      if (sendRequestRef.current !== requestId || activeIdRef.current !== conversationId) return;
      const executionState = messageExecutionState(result.execution);
      if (executionState === "failed") {
        message.error(`${result.execution.message || "客户端操作失败"}；请先在客户端确认实际结果，避免重复发送`);
        return;
      }
      if (executionState === "pending") {
        message.info("任务已提交，正在排队或执行；请到状态页查看任务结果，并在客户端确认实际结果");
        return;
      }

      if (action === "test") {
        message.info("输入测试的 GUI 操作已完成，请在客户端确认输入内容（未发送）");
        return;
      }

      message.info("发送任务的 GUI 操作已完成，请在客户端确认消息是否实际发送；草稿已保留");
    } catch {
      if (sendRequestRef.current === requestId && activeIdRef.current === conversationId) message.warning("提交结果未知，请先查看任务状态并在客户端确认，避免立即重发；草稿已保留");
    } finally {
      setSendingConversationId((current) => current === conversationId ? undefined : current);
    }
  }, [activeConversation?.id, backend, draft, sendingConversationId, message]);

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
      setActiveConversation((current) => current?.id === conversationId ? { ...current, analysis } : current);
      setAnalysisState({ loading: false });
    } catch {
      if (analysisRequestRef.current === requestId && activeIdRef.current === conversationId) {
        setAnalysisState({ loading: false, error: "会话分析失败" });
        message.error("会话分析失败");
      }
    }
  }, [activeConversation?.id, backend, message]);

  const activeCard = useMemo(() => activeConversation?.messages.find((item) => item.card?.id === activeCardId)?.card, [activeCardId, activeConversation]);

  return {
    conversations,
    activeConversation,
    loading,
    detailLoading,
    draft,
    setDraft,
    sending: activeConversationId !== undefined && sendingConversationId === activeConversationId,
    selectConversation,
    translate,
    translateConversation,
    retranslateConversation,
    translationVisible,
    toggleTranslation,
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
    sendMessage,
    groupMode,
    setGroupMode,
    activeCard,
    setActiveCardId,
  };
}
