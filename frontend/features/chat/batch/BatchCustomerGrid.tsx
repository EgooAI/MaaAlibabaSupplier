"use client";

import { DownOutlined, HistoryOutlined, RightOutlined } from "@ant-design/icons";
import { Avatar, Button, Checkbox, Tag, Tooltip, Typography } from "antd";
import type { TableProps } from "antd";
import { useMemo, useState } from "react";
import type { Key } from "react";
import { AppTable } from "@/components/AppTable";
import { avatarColorOf, avatarInitialOf } from "@/domain/chat/avatarModel";
import { conversationTimeLabel } from "@/domain/chat/chatModel";
import { customerLabelOf, groupCustomers } from "@/domain/portrait/customerGroups";
import { lostReasonLabel, portraitStageColor, portraitStageLabel, portraitTagLabel } from "@/domain/portrait/portraitModel";
import { formatDateTime } from "@/domain/time";
import { SessionRows } from "./SessionRows";
import type { CustomerGroup } from "@/domain/portrait/customerGroups";
import type { Conversation } from "@/types/chatCanonical";

type BatchCustomerGridProps = {
  conversations: Conversation[];
  selectedIds: string[];
  loading?: boolean;
  onToggleSessions: (sessionIds: string[], selected: boolean) => void;
  onToggleAll: (selected: boolean) => void;
  onOpenCustomer: (key: string) => void;
};

export function BatchCustomerGrid({ conversations, selectedIds, loading = false, onToggleSessions, onToggleAll, onOpenCustomer }: BatchCustomerGridProps) {
  const groups = useMemo(() => groupCustomers(conversations), [conversations]);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const [expandedKeys, setExpandedKeys] = useState<readonly Key[]>([]);
  const allSelected = conversations.length > 0 && conversations.every((conversation) => selectedSet.has(conversation.id));
  const someSelected = !allSelected && conversations.some((conversation) => selectedSet.has(conversation.id));

  const rowSelection: TableProps<CustomerGroup>["rowSelection"] = {
    columnWidth: 40,
    selectedRowKeys: groups.filter((group) => group.sessionIds.every((id) => selectedSet.has(id))).map((group) => group.key),
    columnTitle: (
      <Checkbox
        aria-label="全选当前页"
        checked={allSelected}
        indeterminate={someSelected}
        onChange={(event) => onToggleAll(event.target.checked)}
      />
    ),
    renderCell: (_checked, group) => {
      const selectedCount = group.sessionIds.filter((id) => selectedSet.has(id)).length;
      const all = selectedCount > 0 && selectedCount === group.sessionIds.length;
      return (
        <Checkbox
          aria-label={`选择 ${customerLabelOf(group)}`}
          checked={all}
          indeterminate={selectedCount > 0 && !all}
          onClick={(event) => event.stopPropagation()}
          onChange={(event) => onToggleSessions(group.sessionIds, event.target.checked)}
        />
      );
    },
  };

  const columns: TableProps<CustomerGroup>["columns"] = [
    {
      title: "客户",
      key: "customer",
      width: 220,
      fixed: "left",
      render: (_, group) => <CustomerCell group={group} />,
    },
    {
      title: "国家",
      key: "country",
      width: 90,
      render: (_, group) => (group.cid === null ? <Muted /> : <span className="text-xs">{group.representative.customer.country || "—"}</span>),
    },
    {
      title: "业务阶段",
      key: "stage",
      width: 116,
      render: (_, group) => (group.cid === null ? <Muted /> : (
        <Tag color={portraitStageColor(group.representative.customer.businessStage)} className="!m-0 !text-[11px]">
          {portraitStageLabel(group.representative.customer.businessStage)}
        </Tag>
      )),
    },
    {
      title: "业务标签",
      key: "tags",
      width: 200,
      render: (_, group) => <TagsCell group={group} />,
    },
    {
      title: "流失信息",
      key: "lost",
      width: 150,
      render: (_, group) => <LostCell group={group} />,
    },
    {
      title: "会话数",
      key: "sessions",
      width: 80,
      render: (_, group) => (
        <Button
          type="link"
          size="small"
          className="!px-0"
          title="展开 / 收起会话"
          onClick={(event) => {
            event.stopPropagation();
            setExpandedKeys((keys) => (keys.includes(group.key) ? keys.filter((key) => key !== group.key) : [...keys, group.key]));
          }}
        >
          {group.sessionIds.length}
        </Button>
      ),
    },
    {
      title: "未读/待回复",
      key: "badges",
      width: 132,
      render: (_, group) => (
        <div className="flex flex-wrap gap-1">
          {group.unread > 0 ? <Tag color="blue" className="!m-0 !text-[11px]">未读 {group.unread}</Tag> : null}
          {group.needsReply > 0 ? <Tag color="orange" className="!m-0 !text-[11px]">待回复 {group.needsReply}</Tag> : null}
          {!group.unread && !group.needsReply ? <Muted /> : null}
        </div>
      ),
    },
    {
      title: "最近消息",
      key: "latest",
      width: 220,
      render: (_, group) => (
        <div className="min-w-0">
          <div className="truncate text-xs text-slate-500" title={group.representative.latestMessage}>{group.representative.latestMessage || "—"}</div>
          <div className="text-[11px] text-slate-400">{conversationTimeLabel(group.representative.updatedAt)}</div>
        </div>
      ),
    },
    {
      title: "画像版本",
      key: "version",
      width: 128,
      render: (_, group) => <VersionCell group={group} />,
    },
    {
      title: "历史",
      key: "history",
      width: 64,
      render: (_, group) => (
        <Button
          type="text"
          size="small"
          aria-label="查看操作历史"
          icon={<HistoryOutlined />}
          onClick={(event) => {
            event.stopPropagation();
            onOpenCustomer(group.key);
          }}
        />
      ),
    },
  ];

  return (
    <AppTable<CustomerGroup>
      rowKey="key"
      size="small"
      loading={loading}
      dataSource={groups}
      columns={columns}
      pagination={false}
      scroll={{ x: 1420 }}
      rowSelection={rowSelection}
      expandable={{
        expandedRowKeys: expandedKeys,
        onExpandedRowsChange: setExpandedKeys,
        expandedRowRender: (group) => <SessionRows conversations={group.conversations} />,
        expandIcon: ({ expanded, onExpand, record }) => (
          <Button
            type="text"
            size="small"
            aria-label={expanded ? "收起会话" : "展开会话"}
            icon={expanded ? <DownOutlined /> : <RightOutlined />}
            onClick={(event) => onExpand(record, event)}
          />
        ),
      }}
      onRow={(group) => ({
        className: "cursor-pointer",
        onClick: (event) => {
          const target = event.target as HTMLElement;
          if (target.closest("button, a, .ant-checkbox-wrapper")) return;
          onOpenCustomer(group.key);
        },
      })}
    />
  );
}

function CustomerCell({ group }: { group: CustomerGroup }) {
  const customer = group.representative.customer;
  const unresolved = group.cid === null;
  const sub = unresolved ? `${group.sessionIds.length} 个会话 · cid 缺失` : [customer.aliId, customer.company].filter(Boolean).join(" · ");
  return (
    <div className="flex min-w-0 items-center gap-2">
      <Avatar size={28} className="shrink-0" style={{ backgroundColor: unresolved ? "#cbd5e1" : avatarColorOf(customer.id) }}>
        {unresolved ? "?" : avatarInitialOf(customer.name)}
      </Avatar>
      <div className="min-w-0">
        <div className="flex items-center gap-1">
          <Typography.Text strong ellipsis className="!text-[13px]">{unresolved ? "未识别客户" : customer.name}</Typography.Text>
          {unresolved ? <Tag className="!m-0 !text-[11px]">不可维护</Tag> : null}
        </div>
        <div className="truncate text-[11px] text-slate-400" title={sub}>{sub}</div>
      </div>
    </div>
  );
}

function TagsCell({ group }: { group: CustomerGroup }) {
  if (group.cid === null) return <Muted />;
  const tags = group.representative.customer.businessTags;
  if (!tags.length) return <Muted />;
  const shown = tags.slice(0, 3);
  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((tag) => (
        <Tag key={tag.tag_id} className="!m-0 !text-[11px]">{portraitTagLabel(tag)}</Tag>
      ))}
      {tags.length > shown.length ? <span className="text-[11px] text-slate-400">+{tags.length - shown.length}</span> : null}
    </div>
  );
}

function LostCell({ group }: { group: CustomerGroup }) {
  if (group.cid === null) return <Muted />;
  const customer = group.representative.customer;
  if (customer.businessStage !== "lost" || !customer.lostReason) return <Muted />;
  return (
    <div className="flex items-center gap-1">
      <Tag color="red" className="!m-0 !text-[11px]">{lostReasonLabel(customer.lostReason)}</Tag>
      {customer.lostNote ? (
        <Tooltip title={customer.lostNote}>
          <span className="text-[11px] text-slate-400 underline decoration-dotted">备注</span>
        </Tooltip>
      ) : null}
    </div>
  );
}

function VersionCell({ group }: { group: CustomerGroup }) {
  if (group.cid === null) return <Muted>不可维护</Muted>;
  const customer = group.representative.customer;
  if (!customer.businessVersion) return <Muted>未维护</Muted>;
  return (
    <div className="text-xs">
      v{customer.businessVersion}
      {customer.lostAt ? <span className="text-[11px] text-slate-400"> · {formatDateTime(customer.lostAt)}</span> : null}
    </div>
  );
}

function Muted({ children = "—" }: { children?: string }) {
  return <span className="text-xs text-slate-300">{children}</span>;
}
