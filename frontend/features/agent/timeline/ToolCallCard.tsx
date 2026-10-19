"use client";

import { CopyOutlined, DownOutlined, MessageOutlined, UpOutlined } from "@ant-design/icons";
import { Button, Space, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { renderContentHtml } from "@/domain/render/markdown";
import { toolRendererOf, type ToolRendererProps } from "./toolRenderers";

/** One tool call: name, status, timing, argument summary and a collapsible result. */
export function ToolCallCard({ call, simulated }: ToolRendererProps) {
  // Failures start open so the reason is visible without a click; a successful
  // call starts collapsed. The user's toggle wins after the first interaction.
  const [open, setOpen] = useState(!call.ok);
  const renderer = toolRendererOf(call.name);
  const summary = renderer?.summarize?.(call.arguments ?? {}) ?? "";

  return (
    <div className={`rounded-lg border ${call.ok ? "border-slate-200 bg-slate-50/60" : "border-red-200 bg-red-50/40"}`}>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="text-slate-500">{renderer?.icon ?? <MessageOutlined />}</span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <Typography.Text strong className="text-xs">{call.name}</Typography.Text>
            <Tag color={call.ok ? "green" : "red"}>{call.ok ? "成功" : "失败"}</Tag>
            {simulated ? <Tag color="gold">模拟</Tag> : null}
            {typeof call.durationMs === "number" ? (
              <Typography.Text type="secondary" className="text-[11px]">{Math.round(call.durationMs)} ms</Typography.Text>
            ) : null}
            {call.round ? <Typography.Text type="secondary" className="text-[11px]">第 {call.round} 轮</Typography.Text> : null}
          </span>
          {summary ? <span className="mt-0.5 block truncate text-xs text-slate-500">{summary}</span> : null}
        </span>
        {open ? <UpOutlined className="text-xs text-slate-400" /> : <DownOutlined className="text-xs text-slate-400" />}
      </button>
      {open ? (
        <div className="border-t border-slate-200 px-3 py-2">
          <Space orientation="vertical" size={4} className="w-full">
            <div className="grid grid-cols-[minmax(72px,auto)_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
              {Object.entries(call.arguments ?? {}).map(([key, value]) => (
                <div key={key} className="contents">
                  <span className="text-slate-400">{key}</span>
                  <span className="whitespace-pre-wrap break-words text-slate-600">{formatValue(value)}</span>
                </div>
              ))}
            </div>
            {call.ok ? (
              renderer ? (
                renderer.render({ call, simulated })
              ) : (
                <pre className="max-h-60 overflow-auto rounded bg-white p-2 text-[11px] leading-relaxed text-slate-600">
                  {formatValue(call.content)}
                </pre>
              )
            ) : (
              <Typography.Text type="danger" className="text-xs">{call.error ?? "工具执行失败"}</Typography.Text>
            )}
          </Space>
        </div>
      ) : null}
    </div>
  );
}

/**
 * ``reply_customer`` renders as the customer-facing bubble it proposes, not as
 * raw parameters: the reply body first, the Chinese reference under it.
 */
export function ReplyCustomerResult({ call, simulated }: ToolRendererProps) {
  const args = call.arguments ?? {};
  const reply = typeof args.reply === "string" ? args.reply : "";
  const zh = typeof args.zh === "string" ? args.zh : "";
  const tone = typeof args.tone === "string" ? args.tone : "";
  if (!call.ok) {
    return <Typography.Text type="danger">被拒绝：{call.error ?? "工具执行失败"}</Typography.Text>;
  }
  if (!reply) {
    return <Typography.Text type="secondary">未返回回复内容。</Typography.Text>;
  }
  return (
    <div className="rounded-xl border border-blue-100 bg-white p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Tag color={simulated ? "gold" : "blue"}>{simulated ? "模拟" : "待确认"}</Tag>
        {tone ? <Tag>{tone}</Tag> : null}
        <Tooltip title="复制回复正文">
          <Button
            size="small"
            type="text"
            icon={<CopyOutlined />}
            aria-label="复制回复正文"
            onClick={() => void navigator.clipboard?.writeText(reply)}
          />
        </Tooltip>
      </div>
      <div className="whitespace-pre-wrap break-words text-sm" dangerouslySetInnerHTML={renderContentHtml(reply, "rich-text")} />
      {zh && zh !== reply ? (
        <div className="mt-2 border-t border-slate-100 pt-2 text-xs text-slate-500">
          <span className="mr-1 text-slate-400">中文参考</span>
          <span className="whitespace-pre-wrap break-words">{zh}</span>
        </div>
      ) : null}
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
