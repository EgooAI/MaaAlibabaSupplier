"use client";

import { App } from "antd";
import { useMemo, useState } from "react";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import type { Conversation } from "@/types/chatCanonical";
import { useConversationSummaries } from "./useConversationSummaries";
import type { ConversationQuery } from "@/types/inbox";

export function useBatchManagement(initialQuery: ConversationQuery = {}) {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const summaries = useConversationSummaries(initialQuery);
  const { conversations, loading, refreshError, refreshPending, selectionVersion, pagePending } = summaries;
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedVersion, setSelectedVersion] = useState(selectionVersion);
  if (selectedVersion !== selectionVersion) {
    setSelectedVersion(selectionVersion);
    setSelectedIds([]);
  }
  const [groupMode, setGroupMode] = useState<ConversationGroupMode>("time");
  const [exporting, setExporting] = useState(false);
  const availableIds = useMemo(() => new Set(conversations.map((conversation) => conversation.id)), [conversations]);
  const visibleSelectedIds = useMemo(() => pagePending ? [] : selectedIds.filter((id) => availableIds.has(id)), [availableIds, selectedIds, pagePending]);

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
      const archiveName = result.archive_name ?? result.archiveName;
      const isZip = Boolean(archiveName);
      const blob = isZip
        ? new Blob([Uint8Array.from(atob(result.content), (char) => char.charCodeAt(0))], { type: "application/zip" })
        : new Blob([result.content], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = archiveName ?? result.file_name ?? result.fileName;
      link.click();
      URL.revokeObjectURL(url);
      message.success(isZip ? "已导出 ZIP 文件" : "已导出 TXT 文件");
    } catch (error: unknown) {
      if (!(error instanceof AccountChangedError)) message.error(error instanceof Error ? error.message : "会话导出失败");
    } finally {
      setExporting(false);
    }
  }

  return {
    ...summaries,
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
    refreshError,
    refreshPending,
  };
}

export type BatchConversation = Conversation;
