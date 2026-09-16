"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { mergeConversationDetail, mergeMessageTranslations, messageExecutionState } from "@/domain/chat/chatModel";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import { backend } from "@/services/client";
import type { AssistantSuggestion, ChatMessage, ConversationDetail } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";

type AnalysisState = {
  loading: boolean;
  error?: string;
};

export function useChatWorkbench() {
  const { message } = App.useApp();
  const { conversations, loading, reload, revision } = useConversationSummaries();
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

  const draft = activeConversationId ? drafts[activeConversationId] ?? "" : "";

  const setDraft = useCallback((value: string) => {
    const id = activeIdRef.current;
    if (!id) return;
    setDrafts((current) => ({ ...current, [id]: value }));
  }, []);

  const selectConversation = useCallback(async (id: string) => {
    const requestId = detailRequestRef.current + 1;
    detailRequestRef.current = requestId;
    activeIdRef.current = id;
    setActiveConversationId(id);
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
  }, [message]);

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
  }, []);

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
  }, [activeConversation?.id, message]);

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
  }, [activeConversation?.id, activeConversation?.messages, message]);

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
  }, [activeConversation?.id, message]);

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
        message.error(result.execution.message || "回复发送失败");
        return;
      }
      if (executionState === "pending") {
        message.info(result.execution.message || "回复任务已提交");
        return;
      }

      if (action === "test") {
        message.success("已填入客户端输入框（测试，未发送）");
        return;
      }

      setActiveConversation((current) => {
        if (!current || current.id !== conversationId) return current;
        return mergeConversationDetail(current, result.conversation);
      });
      setDrafts((current) => {
        if (current[conversationId]?.trim() !== submittedDraft) return current;
        const next = { ...current };
        delete next[conversationId];
        return next;
      });
      await reload();
      message.success("回复已发送");
    } catch {
      if (sendRequestRef.current === requestId && activeIdRef.current === conversationId) message.error("回复发送失败");
    } finally {
      setSendingConversationId((current) => current === conversationId ? undefined : current);
    }
  }, [activeConversation?.id, draft, sendingConversationId, message, reload]);

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
    } catch {
      message.error("跳转任务提交失败");
    }
  }, [activeConversation, message]);

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
  }, [activeConversation?.id, message]);

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
