"use client";

import { Segmented, Space, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { buildPromptDiff, type PromptDiffRow, type PromptDiffSegment } from "@/domain/agent/promptDiff";

function renderSegments(segments: PromptDiffSegment[] | undefined, fallback: string) {
  if (!segments) return fallback;
  return segments.map((segment, index) => (
    segment.kind === "same"
      ? <span key={index}>{segment.text}</span>
      : <mark key={index} className={segment.kind === "added" ? "bg-green-200 text-inherit" : "bg-red-200 text-inherit"}>{segment.text}</mark>
  ));
}

function SideBySideRow({ row }: { row: PromptDiffRow }) {
  const leftClass = row.kind === "added" ? "opacity-40" : row.kind === "changed" || row.kind === "removed" ? "bg-red-50" : "";
  const rightClass = row.kind === "removed" ? "opacity-40" : row.kind === "changed" || row.kind === "added" ? "bg-green-50" : "";
  return (
    <div className="grid grid-cols-2 border-b border-slate-100 last:border-b-0">
      <div className={`break-words whitespace-pre-wrap px-2 py-0.5 ${leftClass}`}>{row.before === null ? "" : renderSegments(row.beforeSegments, row.before)}</div>
      <div className={`break-words whitespace-pre-wrap border-l border-slate-100 px-2 py-0.5 ${rightClass}`}>{row.after === null ? "" : renderSegments(row.afterSegments, row.after)}</div>
    </div>
  );
}

function UnifiedRow({ row }: { row: PromptDiffRow }) {
  if (row.kind === "context") {
    return <div className="break-words whitespace-pre-wrap px-2 py-0.5 text-slate-500">{row.before}</div>;
  }
  return (
    <>
      {row.before !== null ? (
        <div className="break-words whitespace-pre-wrap bg-red-50 px-2 py-0.5">
          <span className="mr-1 text-red-400 select-none">−</span>
          {renderSegments(row.beforeSegments, row.before)}
        </div>
      ) : null}
      {row.after !== null ? (
        <div className="break-words whitespace-pre-wrap bg-green-50 px-2 py-0.5">
          <span className="mr-1 text-green-500 select-none">+</span>
          {renderSegments(row.afterSegments, row.after)}
        </div>
      ) : null}
    </>
  );
}

/** Read-only prompt diff: side-by-side by default, unified for narrow screens. */
export function PromptDiffView({ before, after, maxHeight = 380 }: { before: string; after: string; maxHeight?: number }) {
  const diff = useMemo(() => buildPromptDiff(before, after), [before, after]);
  const [mode, setMode] = useState<"side" | "unified">("side");
  const identical = diff.rows.every((row) => row.kind === "context");

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 pb-2">
        <Space size="small">
          <Tag color="green">+{diff.addedLines}</Tag>
          <Tag color="red">−{diff.removedLines}</Tag>
        </Space>
        <Segmented
          size="small"
          value={mode}
          onChange={(value) => setMode(value as "side" | "unified")}
          options={[{ label: "左右对照", value: "side" }, { label: "统一视图", value: "unified" }]}
        />
      </div>
      <div className="overflow-auto rounded border border-slate-200 bg-white font-mono text-xs" style={{ maxHeight }}>
        {identical ? (
          <Typography.Text type="secondary" className="block px-3 py-4">两段内容一致，没有差异。</Typography.Text>
        ) : mode === "side" ? (
          diff.rows.map((row, index) => <SideBySideRow key={index} row={row} />)
        ) : (
          diff.rows.map((row, index) => <UnifiedRow key={index} row={row} />)
        )}
      </div>
    </div>
  );
}
