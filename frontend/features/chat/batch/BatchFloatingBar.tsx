"use client";

import type { BatchPanelAction } from "@/domain/portrait/batchPreview";

/** 选中会话后常驻的底部操作条（快捷动作与工具栏同源，均进入配置面板）。 */
export function BatchFloatingBar({ selectedCount, selectedCustomerCount, busy, exporting, exportingPortraits, onOpenPanel, onExportPortraits, onExport, onClear }: {
  selectedCount: number;
  selectedCustomerCount: number;
  busy: boolean;
  exporting: boolean;
  exportingPortraits: boolean;
  onOpenPanel: (action: BatchPanelAction) => void;
  onExportPortraits: () => void;
  onExport: () => void;
  onClear: () => void;
}) {
  if (!selectedCount) return null;
  const actionClass = "rounded-md px-2.5 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <div className="fixed bottom-5 left-1/2 z-40 -translate-x-1/2">
      <div className="flex items-center gap-1 rounded-xl bg-slate-900 py-1.5 pl-4 pr-2 shadow-xl">
        <span className="mr-2 border-r border-slate-700 pr-3 text-xs text-slate-300">
          已选 <b className="text-blue-300">{selectedCount}</b> 个会话 · <b className="text-blue-300">{selectedCustomerCount}</b> 位客户
        </span>
        <button type="button" className={actionClass} disabled={busy} onClick={() => onOpenPanel("stage")}>设置阶段</button>
        <button type="button" className={actionClass} disabled={busy} onClick={() => onOpenPanel("add-tag")}>加标签</button>
        <button type="button" className={actionClass} disabled={busy} onClick={() => onOpenPanel("remove-tag")}>移除标签</button>
        <button type="button" className={actionClass} disabled={exportingPortraits} onClick={onExportPortraits}>导出画像</button>
        <button type="button" className={actionClass} disabled={exporting} onClick={onExport}>导出聊天</button>
        <button type="button" className="rounded-md px-2.5 py-1 text-xs text-red-300 hover:bg-slate-700" onClick={onClear}>清除</button>
      </div>
    </div>
  );
}
