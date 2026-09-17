"use client";

import { useState } from "react";
import { Button, Checkbox, Input, Select, Space, Typography } from "antd";
import type { ConversationPage, ConversationQuery } from "@/types/inbox";
import { replyStates } from "@/domain/chat/inboxModel";
import { statusLabel } from "@/domain/chat/chatModel";

export function ConversationFilters({ query, onChange }: { query: ConversationQuery; onChange: (query: ConversationQuery) => void }) {
  const [draft, setDraft] = useState(query);
  return <form onSubmit={(event) => { event.preventDefault(); onChange(draft); }} className="mb-3 flex flex-col gap-2">
    <Select aria-label="搜索范围" value={draft.search_scope ?? "all"} onChange={(search_scope) => setDraft({ ...draft, search_scope })} options={[
      { value: "all", label: "客户资料与消息" }, { value: "customer", label: "仅客户资料" }, { value: "messages", label: "仅消息正文" },
    ]} />
    <Input aria-label="搜索内容" maxLength={200} value={draft.q ?? ""} onChange={(event) => setDraft({ ...draft, q: event.target.value || undefined })} placeholder={draft.search_scope === "messages" ? "搜索消息正文" : draft.search_scope === "customer" ? "搜索姓名、公司、客户标识" : "搜索客户资料或消息正文"} allowClear />
    <Space.Compact className="w-full">
      <Input aria-label="国家" value={draft.country ?? ""} onChange={(event) => setDraft({ ...draft, country: event.target.value || undefined })} placeholder="国家" allowClear />
      <Input aria-label="客户资料标签" value={draft.tag ?? ""} onChange={(event) => setDraft({ ...draft, tag: event.target.value || undefined })} placeholder="客户资料标签" allowClear />
    </Space.Compact>
    <Select aria-label="回复状态" allowClear placeholder="全部回复状态" value={draft.reply_state} onChange={(reply_state) => setDraft({ ...draft, reply_state })} options={replyStates.map((value) => ({ value, label: statusLabel(value) }))} />
    <Space wrap>
      <Checkbox checked={draft.unread === true} onChange={(event) => setDraft({ ...draft, unread: event.target.checked ? true : undefined })}>本工作台未读</Checkbox>
      <Checkbox checked={draft.overdue === true} onChange={(event) => setDraft({ ...draft, overdue: event.target.checked ? true : undefined })}>超时</Checkbox>
      <Button htmlType="submit" type="primary" size="small">应用筛选</Button>
      <Button size="small" onClick={() => { setDraft({ search_scope: "all" }); onChange({ search_scope: "all" }); }}>重置</Button>
    </Space>
  </form>;
}

export function ConversationPaging({ page, pending, onChange }: { page?: ConversationPage; pending: boolean; onChange: (offset: number) => void }) {
  return <Space wrap className="my-3">
    <Typography.Text type="secondary">{page ? `筛选结果 ${page.total} 个 · 第 ${Math.floor(page.offset / page.limit) + 1} 页 · 本页 ${page.items.length} 个` : pending ? "正在读取会话" : "读取失败，等待重试"}</Typography.Text>
    <Button size="small" disabled={pending || !page || page.offset === 0} onClick={() => page && onChange(Math.max(0, page.offset - page.limit))}>上一页</Button>
    <Button size="small" disabled={pending || !page || page.offset + page.limit >= page.total} onClick={() => page && onChange(page.offset + page.limit)}>下一页</Button>
  </Space>;
}
