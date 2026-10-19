"use client";

import { CopyOutlined, DownOutlined, MessageOutlined, UpOutlined } from "@ant-design/icons";
import { Button, Space, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import { renderContentHtml } from "@/domain/render/markdown";
import type { AgentToolCallRecord } from "@/types/agent";
import { toolRendererOf, type ToolRendererProps } from "./toolRenderers";

/** One tool call: name, status, timing, argument summary and a collapsible result. */
export function ToolCallCard({ call, simulated, intent }: ToolRendererProps) {
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
            {intent && !simulated ? (
              <Tag color={intentColor(intent.status)}>{intentLabel(intent.status, false)}</Tag>
            ) : null}
            {typeof call.durationMs === "number" ? (
              <Typography.Text type="secondary" className="text-[11px]">{Math.round(call.durationMs)} ms</Typography.Text>
            ) : null}
            {typeof call.originalChars === "number" ? (
              <Tooltip title={`结果已截断，原始长度 ${call.originalChars} 字符`}>
                <Tag color="orange">已截断</Tag>
              </Tooltip>
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
                renderer.render({ call, simulated, intent })
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
export function ReplyCustomerResult({ call, simulated, intent }: ToolRendererProps) {
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
        <Tag color={simulated ? "gold" : intentColor(intent?.status)}>{intentLabel(intent?.status, simulated)}</Tag>
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

/**
 * ``query_product`` renders the cached card summaries instead of raw JSON: each
 * hit shows title, price, MOQ and a link, plus the coverage note the tool always
 * returns so a miss is not read as "the product does not exist".
 */
export function ProductQueryResult({ call }: { call: AgentToolCallRecord }) {
  const payload = (call.content && typeof call.content === "object" ? call.content : {}) as {
    total?: number;
    returned?: number;
    items?: Array<Record<string, unknown>>;
    coverage?: string;
  };
  const items = Array.isArray(payload.items) ? payload.items : [];
  const coverage = typeof payload.coverage === "string" ? payload.coverage : "";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-2">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>命中 {payload.total ?? items.length} 条</span>
        {typeof payload.returned === "number" && payload.returned < (payload.total ?? 0) ? (
          <Tag color="blue">已显示前 {payload.returned} 条</Tag>
        ) : null}
      </div>
      {items.length ? (
        <ul className="flex flex-col gap-2">
          {items.map((item, index) => {
            const title = typeof item.title === "string" ? item.title : "(未命名商品)";
            const productId = typeof item.product_id === "string" ? item.product_id : "";
            const price = typeof item.price === "string" ? item.price : "";
            const moq = typeof item.moq === "string" ? item.moq : "";
            const url = typeof item.url === "string" ? item.url : "";
            return (
              <li key={`${productId || title}-${index}`} className="border-t border-slate-100 pt-1 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Typography.Text strong className="text-xs">{title}</Typography.Text>
                  {item.expired === true ? <Tag color="default">已下架</Tag> : null}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-slate-500">
                  {price ? <span>{price}</span> : null}
                  {moq ? <span>MOQ {moq}</span> : null}
                  {productId ? <span>ID {productId}</span> : null}
                </div>
                {url ? (
                  <a className="text-[11px]" href={url} target="_blank" rel="noopener noreferrer">
                    查看商品
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <Typography.Text type="secondary" className="text-xs">没有匹配的已缓存商品。</Typography.Text>
      )}
      {coverage ? <div className="mt-2 text-[11px] text-slate-400">{coverage}</div> : null}
    </div>
  );
}

function intentLabel(status: string | undefined, simulated: boolean): string {
  if (simulated) return "模拟";
  if (status === "accepted") return "已采纳";
  if (status === "discarded") return "已丢弃";
  return "待确认";
}

function intentColor(status: string | undefined): string {
  if (status === "accepted") return "green";
  if (status === "discarded") return "default";
  return "blue";
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
