"use client";

import { App } from "antd";
import { useMemo, useState } from "react";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import type { BatchPanelAction, BatchPanelView } from "@/domain/portrait/batchPreview";
import { portraitRequestId } from "@/domain/portrait/portraitModel";
import type { Conversation } from "@/types/chatCanonical";
import type { PortraitChangesInput, PortraitTargetInput } from "@/types/portrait";
import { useConversationSummaries } from "./useConversationSummaries";
import { usePortraitTags } from "./usePortraitTags";
import type { ConversationQuery } from "@/types/inbox";

export function useBatchManagement(initialQuery: ConversationQuery = {}) {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const summaries = useConversationSummaries(initialQuery);
  const { conversations, loading, refreshError, refreshPending, selectionVersion, pagePending } = summaries;
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [panel, setPanel] = useState<{ action: BatchPanelView; sessionIds: string[] } | null>(null);
  const [selectedVersion, setSelectedVersion] = useState(selectionVersion);
  if (selectedVersion !== selectionVersion) {
    setSelectedVersion(selectionVersion);
    setSelectedIds([]);
    setPanel(null);
  }
  const [groupMode, setGroupMode] = useState<ConversationGroupMode>("time");
  const [exporting, setExporting] = useState(false);
  const [exportingPortraits, setExportingPortraits] = useState(false);
  const [portraitBusy, setPortraitBusy] = useState(false);
  const { tags: portraitTags, reload: reloadPortraitTags } = usePortraitTags(summaries.observedTagRevision);
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

  function openPanel(action: BatchPanelView, sessionIds?: string[]) {
    const ids = sessionIds ?? visibleSelectedIds;
    if (!ids.length) {
      message.warning("请先选择要操作的会话");
      return;
    }
    setPanel({ action, sessionIds: ids });
  }

  function changePanelAction(action: BatchPanelAction) {
    setPanel((current) => (current ? { ...current, action } : current));
  }

  function closePanel() {
    setPanel(null);
  }

  async function exportSelected() {
    if (exporting) return;
    const ids = panelSessionIds();
    if (!ids.length) {
      message.warning("请先选择要导出的会话");
      return;
    }
    setExporting(true);
    try {
      // Conversation ids are numeric session ids displayed as strings; the API
      // takes integers and reports anything skipped server-side via `missing`.
      const parsed = ids.map((id) => Number(id));
      const valid = parsed.filter((sid) => Number.isInteger(sid) && sid >= 1);
      const localSkipped = parsed.length - valid.length;
      const result = await backend.exportConversations({ conversationIds: valid });
      const archiveName = result.archive_name;
      const isZip = Boolean(archiveName);
      const blob = isZip
        ? new Blob([Uint8Array.from(atob(result.content), (char) => char.charCodeAt(0))], { type: "application/zip" })
        : new Blob([result.content], { type: "text/markdown;charset=utf-8" });
      downloadBlob(blob, archiveName ?? "conversation-export.md");
      const missing = (result.missing?.length ?? 0) + localSkipped;
      if (missing) message.warning(`有 ${missing} 个所选会话未导出（不可见或无法定位）`);
      message.success(isZip ? "已导出 ZIP 文件" : "已导出 Markdown 文件");
    } catch (error: unknown) {
      if (!(error instanceof AccountChangedError)) message.error(error instanceof Error ? error.message : "会话导出失败");
    } finally {
      setExporting(false);
    }
  }

  /** 面板打开时以面板自身的目标为准（行内入口可能不带选择），否则回退当前选择。 */
  function panelSessionIds() {
    const source = panel?.sessionIds ?? visibleSelectedIds;
    return source.filter((id) => availableIds.has(id));
  }

  function portraitTargets() {
    const wanted = new Set(panelSessionIds());
    const targets: PortraitTargetInput[] = [];
    let skipped = 0;
    for (const conversation of conversations) {
      if (!wanted.has(conversation.id)) continue;
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

  async function applyPortraitChanges(changes: PortraitChangesInput, description: string) {
    if (portraitBusy) return;
    const { targets, skipped } = portraitTargets();
    if (!targets.length) {
      message.warning("所选会话缺少可维护的客户身份，无法修改画像");
      return;
    }
    if (targets.length > 50) {
      message.warning("单批最多 50 个会话，请减少选择后重试");
      return;
    }
    setPortraitBusy(true);
    let accountChanged = false;
    try {
      const result = await backend.savePortraitBatch({ request_id: portraitRequestId(), targets, changes });
      const changed = result.changed.length;
      const unchanged = result.unchanged.length;
      message.success(`画像已保存：${description}；${changed} 位客户已更新${unchanged ? `，${unchanged} 位无需修改` : ""}${skipped ? `；${skipped} 个会话因身份无法确定被跳过` : ""}`);
    } catch (error) {
      if (error instanceof AccountChangedError) {
        accountChanged = true;
      } else {
        message.error(error instanceof Error ? error.message : "画像修改失败");
      }
    } finally {
      setPortraitBusy(false);
      // 提交后（无论成败）目标版本可能已过期，关闭面板并重读列表。
      setPanel(null);
      if (!accountChanged) summaries.changeQuery(summaries.query);
    }
  }

  async function exportPortraits() {
    if (exportingPortraits) return;
    const { targets, skipped } = portraitTargets();
    if (!targets.length) {
      message.warning("所选会话缺少可维护的客户身份，无法导出客户资料");
      return;
    }
    if (targets.length > 100) {
      message.warning("单次最多导出 100 个会话，请减少选择后重试");
      return;
    }
    setExportingPortraits(true);
    try {
      const result = await backend.exportPortraits({ conversationIds: targets.map((target) => target.sid) });
      downloadBlob(new Blob([result.content], { type: "text/csv;charset=utf-8" }), result.filename);
      const missing = result.missing.length + skipped;
      message.success(`已导出 ${result.exported} 位客户资料${missing ? `；${missing} 个会话未导出（不可见或身份无法确定）` : ""}`);
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
    reloadPortraitTags,
    groupMode,
    setGroupMode,
    toggleSelected,
    setGroupSelected,
    selectAll,
    invertSelection,
    clearSelection: () => setSelectedIds([]),
    panel,
    openPanel,
    changePanelAction,
    closePanel,
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
