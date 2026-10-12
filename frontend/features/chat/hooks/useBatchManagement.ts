"use client";

import { App } from "antd";
import { useMemo, useState } from "react";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import { portraitRequestId } from "@/domain/portrait/portraitModel";
import type { Conversation } from "@/types/chatCanonical";
import type { PortraitChangesInput, PortraitTargetInput } from "@/types/portrait";
import { useConversationSummaries } from "./useConversationSummaries";
import { usePortraitTags } from "./usePortraitTags";
import type { ConversationQuery } from "@/types/inbox";

export function useBatchManagement(initialQuery: ConversationQuery = {}) {
  const { message, modal } = App.useApp();
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
  const [exportingPortraits, setExportingPortraits] = useState(false);
  const [portraitBusy, setPortraitBusy] = useState(false);
  const { tags: portraitTags } = usePortraitTags();
  const availableIds = useMemo(() => new Set(conversations.map((conversation) => conversation.id)), [conversations]);
  const visibleSelectedIds = useMemo(() => pagePending ? [] : selectedIds.filter((id) => availableIds.has(id)), [availableIds, selectedIds, pagePending]);
  const selectedConversations = useMemo(
    () => conversations.filter((conversation) => visibleSelectedIds.includes(conversation.id)),
    [conversations, visibleSelectedIds],
  );
  const selectedCustomerCount = useMemo(
    () => new Set(selectedConversations.map((conversation) => conversation.customer.cid).filter(Boolean)).size,
    [selectedConversations],
  );

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
      const archiveName = result.archive_name;
      const isZip = Boolean(archiveName);
      const blob = isZip
        ? new Blob([Uint8Array.from(atob(result.content), (char) => char.charCodeAt(0))], { type: "application/zip" })
        : new Blob([result.content], { type: "text/plain;charset=utf-8" });
      downloadBlob(blob, archiveName ?? "conversation-export.txt");
      message.success(isZip ? "已导出 ZIP 文件" : "已导出 TXT 文件");
    } catch (error: unknown) {
      if (!(error instanceof AccountChangedError)) message.error(error instanceof Error ? error.message : "会话导出失败");
    } finally {
      setExporting(false);
    }
  }

  function portraitTargets() {
    const targets: PortraitTargetInput[] = [];
    let skipped = 0;
    for (const conversation of selectedConversations) {
      const cid = conversation.customer.cid;
      if (!cid) {
        skipped += 1;
        continue;
      }
      targets.push({
        sid: Number(conversation.id),
        expected_cid: cid,
        expected_version: conversation.customer.businessVersion,
      });
    }
    return { targets, skipped };
  }

  function applyPortraitChanges(changes: PortraitChangesInput, description: string) {
    if (portraitBusy) return;
    const { targets, skipped } = portraitTargets();
    if (!targets.length) {
      message.warning("所选会话缺少可维护的客户身份，无法修改画像");
      return;
    }
    const customerCount = new Set(targets.map((target) => target.expected_cid)).size;
    modal.confirm({
      title: "确认批量修改客户画像",
      content: `将修改 ${targets.length} 个会话，涉及 ${customerCount} 位客户：${description}。修改不可整体撤回，但会记录变更并可通过再次修改纠正。${skipped ? ` 另有 ${skipped} 个会话因身份无法确定不会被修改。` : ""}`,
      okText: "确认修改",
      cancelText: "取消",
      onOk: async () => {
        setPortraitBusy(true);
        let accountChanged = false;
        try {
          const result = await backend.savePortraitBatch({ request_id: portraitRequestId(), targets, changes });
          const changed = result.changed.length;
          const unchanged = result.unchanged.length;
          message.success(`画像已保存：${changed} 位客户已更新${unchanged ? `，${unchanged} 位无需修改` : ""}`);
        } catch (error) {
          if (error instanceof AccountChangedError) {
            accountChanged = true;
          } else {
            message.error(error instanceof Error ? error.message : "画像修改失败");
          }
        } finally {
          setPortraitBusy(false);
          // Success and failures both leave displayed versions potentially stale.
          if (!accountChanged) summaries.changeQuery(summaries.query);
        }
      },
    });
  }

  async function exportPortraits() {
    if (exportingPortraits) return;
    const { targets, skipped } = portraitTargets();
    if (!targets.length) {
      message.warning("所选会话缺少可维护的客户身份，无法导出客户资料");
      return;
    }
    setExportingPortraits(true);
    try {
      const result = await backend.exportPortraits({ conversationIds: targets.map((target) => target.sid) });
      downloadBlob(new Blob([result.content], { type: "text/csv;charset=utf-8" }), result.filename);
      const missing = result.missing.length + skipped;
      message.success(`已导出 ${result.exported} 位客户资料${missing ? `；${missing} 个会话因身份无法确定未导出` : ""}`);
    } catch (error: unknown) {
      if (!(error instanceof AccountChangedError)) message.error(error instanceof Error ? error.message : "客户资料导出失败");
    } finally {
      setExportingPortraits(false);
    }
  }

  return {
    ...summaries,
    conversations,
    loading,
    selectedIds: visibleSelectedIds,
    selectedCount: visibleSelectedIds.length,
    selectedCustomerCount,
    exporting,
    exportingPortraits,
    portraitBusy,
    portraitTags,
    groupMode,
    setGroupMode,
    toggleSelected,
    setGroupSelected,
    selectAll,
    invertSelection,
    clearSelection: () => setSelectedIds([]),
    exportSelected,
    applyPortraitChanges,
    exportPortraits,
    refreshError,
    refreshPending,
  };
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export type BatchConversation = Conversation;
