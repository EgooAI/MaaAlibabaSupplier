"use client";

import { useState } from "react";
import { FilterOutlined } from "@ant-design/icons";
import { Button, Checkbox, Input, Popover, Select, Space, Typography } from "antd";
import type { ConversationPage, ConversationQuery } from "@/types/inbox";
import { replyStates } from "@/domain/chat/inboxModel";
import { statusLabel } from "@/domain/chat/chatModel";

export function ConversationFilters({ query, onChange }: { query: ConversationQuery; onChange: (query: ConversationQuery) => void }) {
  const [draft, setDraft] = useState(query);
  const [applied, setApplied] = useState(query);
  const [open, setOpen] = useState(false);
  const filterCount = [applied.country, applied.tag, applied.reply_state, applied.unread, applied.overdue, applied.search_scope && applied.search_scope !== "all"].filter(Boolean).length;
  function apply(next: ConversationQuery) {
    setDraft(next);
    setApplied(next);
    onChange(next);
    setOpen(false);
  }
  const filters = <form onSubmit={(event) => { event.preventDefault(); apply(draft); }} className="flex w-[min(280px,calc(100vw-48px))] flex-col gap-3">
    <Typography.Text type="secondary">搜索范围</Typography.Text>
    <Select aria-label="搜索范围" value={draft.search_scope ?? "all"} onChange={(search_scope) => setDraft({ ...draft, search_scope })} options={[
      { value: "all", label: "客户资料与消息" }, { value: "customer", label: "仅客户资料" }, { value: "messages", label: "仅消息正文" },
    ]} />
    <Space.Compact className="w-full">
      <Input aria-label="国家" value={draft.country ?? ""} onChange={(event) => setDraft({ ...draft, country: event.target.value || undefined })} placeholder="国家" allowClear />
      <Input aria-label="客户资料标签" value={draft.tag ?? ""} onChange={(event) => setDraft({ ...draft, tag: event.target.value || undefined })} placeholder="客户资料标签" allowClear />
    </Space.Compact>
    <Select aria-label="回复状态" allowClear placeholder="全部回复状态" value={draft.reply_state} onChange={(reply_state) => setDraft({ ...draft, reply_state })} options={replyStates.map((value) => ({ value, label: statusLabel(value) }))} />
    <Space wrap>
      <Checkbox checked={draft.unread === true} onChange={(event) => setDraft({ ...draft, unread: event.target.checked ? true : undefined })}>本工作台未读</Checkbox>
      <Checkbox checked={draft.overdue === true} onChange={(event) => setDraft({ ...draft, overdue: event.target.checked ? true : undefined })}>超时</Checkbox>
    </Space>
    <div className="flex justify-between border-t border-slate-100 pt-3">
      <Button onClick={() => apply({ search_scope: "all" })}>重置</Button>
      <Button htmlType="submit" type="primary">应用筛选</Button>
    </div>
  </form>;
  return <div className="mb-3 flex shrink-0 items-center gap-2">
    <Input.Search aria-label="搜索内容" maxLength={200} value={draft.q ?? ""} onChange={(event) => setDraft({ ...draft, q: event.target.value || undefined })} onSearch={(q) => apply({ ...applied, q: q || undefined })} placeholder="搜索客户或消息" allowClear />
    <Popover title="筛选会话" content={filters} trigger="click" placement="bottomRight" open={open} onOpenChange={(nextOpen) => { if (nextOpen) setDraft({ ...applied, q: draft.q }); setOpen(nextOpen); }}>
      <Button aria-label={filterCount ? `筛选会话，已应用 ${filterCount} 项` : "筛选会话"} title="筛选会话" type={filterCount ? "primary" : "default"} icon={<FilterOutlined />}>{filterCount || null}</Button>
    </Popover>
  </div>;
}

export function ConversationPaging({ page, pending, onChange }: { page?: ConversationPage; pending: boolean; onChange: (offset: number) => void }) {
  return <div className="mt-3 flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3">
    <Typography.Text type="secondary" className="text-xs">{page ? `共 ${page.total} 个 · ${Math.floor(page.offset / page.limit) + 1} / ${Math.max(1, Math.ceil(page.total / page.limit))} 页` : pending ? "正在读取会话" : "读取失败，等待重试"}</Typography.Text>
    <Space size={4}>
    <Button size="small" disabled={pending || !page || page.offset === 0} onClick={() => page && onChange(Math.max(0, page.offset - page.limit))}>上一页</Button>
    <Button size="small" disabled={pending || !page || page.offset + page.limit >= page.total} onClick={() => page && onChange(page.offset + page.limit)}>下一页</Button>
    </Space>
  </div>;
}
