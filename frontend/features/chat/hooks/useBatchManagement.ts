"use client";

import { App } from "antd";
import { useMemo, useState } from "react";
import { backend } from "@/services/client";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import type { Conversation } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";

export function useBatchManagement() {
  const { message } = App.useApp();
  const { conversations, loading, reload } = useConversationSummaries();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [groupMode, setGroupMode] = useState<ConversationGroupMode>("time");
  const [exporting, setExporting] = useState(false);
  const availableIds = useMemo(() => new Set(conversations.map((conversation) => conversation.id)), [conversations]);
  const visibleSelectedIds = useMemo(() => selectedIds.filter((id) => availableIds.has(id)), [availableIds, selectedIds]);

  function toggleSelected(id: string) {
    setSelectedIds((ids) => (ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id]));
  }

  function selectAll() {
    setSelectedIds(conversations.map((conversation) => conversation.id));
  }

  function invertSelection() {
    setSelectedIds((ids) => conversations.map((conversation) => conversation.id).filter((id) => !ids.includes(id)));
  }

  function setGroupSelected(ids: string[], value: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (value) next.add(id);
        else next.delete(id);
      }
      return conversations.map((conversation) => conversation.id).filter((id) => next.has(id));
    });
  }

  async function exportSelected() {
    if (exporting) return;
    if (!visibleSelectedIds.length) {
      message.warning("请先选择要导出的会话");
      return;
    }
    setExporting(true);
    try {
      const result = await backend.exportConversations({ conversationIds: visibleSelectedIds });
      // mock 后端返回 TXT 文本；真实后端返回 zip 的 base64（archiveName 存在时）。
      const isZip = Boolean(result.archiveName);
      const blob = isZip
        ? new Blob([Uint8Array.from(atob(result.content), (char) => char.charCodeAt(0))], { type: "application/zip" })
        : new Blob([result.content], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.archiveName ?? result.fileName;
      link.click();
      URL.revokeObjectURL(url);
      message.success(isZip ? "已导出 ZIP 文件" : "已导出 TXT 文件");
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "会话导出失败");
    } finally {
      setExporting(false);
    }
  }

  return {
    conversations,
    loading,
    selectedIds: visibleSelectedIds,
    selectedCount: visibleSelectedIds.length,
    exporting,
    groupMode,
    setGroupMode,
    toggleSelected,
    setGroupSelected,
    selectAll,
    invertSelection,
    clearSelection: () => setSelectedIds([]),
    exportSelected,
    reload,
  };
}

export type BatchConversation = Conversation;
