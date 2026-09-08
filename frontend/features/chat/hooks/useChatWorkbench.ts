"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { mergeConversationDetail, mergeMessageTranslations, messageExecutionState } from "@/domain/chat/chatModel";
import { backend } from "@/services/client";
import type { AssistantSuggestion, ChatMessage, ConversationDetail } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";

type AnalysisState = {
  loading: boolean;
  error?: string;
};

export function useChatWorkbench() {
  const { message } = App.useApp();
  const { conversations, loading, reload } = useConversationSummaries();
  const [activeConversationId, setActiveConversationId] = useState<string>();
  const [activeConversation, setActiveConversation] = useState<ConversationDetail>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [sendingConversationId, setSendingConversationId] = useState<string>();
  const [suggestions, setSuggestions] = useState<AssistantSuggestion[]>([]);
  const [suggestionOpen, setSuggestionOpen] = useState(false);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [analysisState, setAnalysisState] = useState<AnalysisState>({ loading: false });
  const [translationVisible, setTranslationVisible] = useState(false);
  const [groupMode, setGroupMode] = useState<"time" | "status">("time");
  const [activeCardId, setActiveCardId] = useState<string>();
  const activeIdRef = useRef<string | undefined>(undefined);
  const detailRequestRef = useRef(0);
  const sendRequestRef = useRef(0);
  const translateRequestRef = useRef(0);
  const suggestionRequestRef = useRef(0);
  const analysisRequestRef = useRef(0);
  const translationVisibleRef = useRef(false);

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
    translationVisibleRef.current = false;
    setTranslationVisible(false);
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

  async function translate(messageItem: ChatMessage, regenerate = false) {
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
  }

  async function translateConversation() {
    const conversationId = activeConversation?.id;
    const messages = activeConversation?.messages ?? [];
    if (!conversationId) return;
    const requestId = translateRequestRef.current + 1;
    translateRequestRef.current = requestId;
    translationVisibleRef.current = true;

    const buyerMessages = messages.filter((item) => item.role === "buyer" && !item.translatedContent);
    if (!buyerMessages.length) {
      setTranslationVisible(true);
      return;
    }

    try {
      const translatedMessages = await Promise.all(
        buyerMessages.map((item) => backend.translateMessage({ conversationId, messageId: item.id, targetLanguage: "zh-CN" })),
      );
      if (translateRequestRef.current !== requestId || activeIdRef.current !== conversationId) return;
      setActiveConversation((current) => {
        if (!current || current.id !== conversationId) return current;
        return { ...current, messages: mergeMessageTranslations(current.messages, translatedMessages) };
      });
      if (translationVisibleRef.current) setTranslationVisible(true);
      message.success("已翻译当前会话");
    } catch {
      if (translateRequestRef.current === requestId && activeIdRef.current === conversationId) message.error("会话翻译失败");
    }
  }

  function toggleTranslation() {
    if (translationVisible) {
      translationVisibleRef.current = false;
      setTranslationVisible(false);
      return;
    }
    void translateConversation();
  }

  async function openSuggestions() {
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
  }

  function insertSuggestion(content: string) {
    setDraft(content);
    setSuggestionOpen(false);
    message.success("已插入到回复框");
  }

  async function sendMessage() {
    const conversationId = activeConversation?.id;
    const submittedDraft = draft.trim();
    if (!conversationId || !submittedDraft || sendingConversationId === conversationId) return;
    const requestId = sendRequestRef.current + 1;
    sendRequestRef.current = requestId;

    setSendingConversationId(conversationId);
    try {
      const result = await backend.sendMessage({ conversationId, content: submittedDraft, action: "send" });
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
  }

  async function analyzeConversation() {
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
  }

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
    translationVisible,
    toggleTranslation,
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
