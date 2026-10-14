"use client";

import { Descriptions, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { BaseDrawer } from "@/components/BaseDrawer";
import { useAccountBackend } from "@/features/account/AccountProvider";
import { customerLabelOf, type CustomerGroup } from "@/domain/portrait/customerGroups";
import { lostReasonLabel, operationSummary, portraitStageColor, portraitStageLabel, portraitTagLabel, portraitTagOptions } from "@/domain/portrait/portraitModel";
import { formatDateTime } from "@/domain/time";
import { SessionRows } from "./SessionRows";
import type { PortraitOperationEntry, PortraitTag } from "@/types/portrait";

/** 批量页只读客户详情：资料 + 画像 + 本页会话 + 操作历史（维护入口在 Phase 3 的配置面板）。 */
export function BatchCustomerDrawer({ group, open, onClose, portraitTags }: {
  group: CustomerGroup | null;
  open: boolean;
  onClose: () => void;
  portraitTags: PortraitTag[];
}) {
  const backend = useAccountBackend();
  const cid = group?.cid ?? null;
  const version = group?.representative.customer.businessVersion ?? 0;
  const [history, setHistory] = useState<{ cid: number; operations: PortraitOperationEntry[] } | null>(null);

  useEffect(() => {
    if (!open || !cid) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await backend.listPortraitOperations(cid);
        if (!cancelled) setHistory({ cid, operations: result.operations });
      } catch {
        // 历史仅作参考，读取失败不打断详情查看。
        if (!cancelled) setHistory({ cid, operations: [] });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [backend, cid, open, version]);
  const operations = history && history.cid === cid ? history.operations : [];

  return (
    <BaseDrawer title="客户详情" open={open && Boolean(group)} onClose={onClose}>
      {group ? <CustomerDetail group={group} operations={operations} portraitTags={portraitTags} /> : null}
    </BaseDrawer>
  );
}

function CustomerDetail({ group, operations, portraitTags }: {
  group: CustomerGroup;
  operations: PortraitOperationEntry[];
  portraitTags: PortraitTag[];
}) {
  const customer = group.representative.customer;
  if (group.cid === null) {
    return (
      <div className="flex flex-col gap-4">
        <Typography.Text type="secondary">
          无法关联平台客户，画像不可维护；聊天与导出不受影响。
        </Typography.Text>
        <Section title={`包含会话（${group.sessionIds.length}）`}>
          <SessionRows conversations={group.conversations} />
        </Section>
        <Section title="操作历史">
          <Typography.Text type="secondary">无操作历史</Typography.Text>
        </Section>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Typography.Title level={5} className="!mb-0">{customerLabelOf(group)}</Typography.Title>
        <Typography.Text type="secondary" className="!text-xs">{[customer.aliId, customer.company].filter(Boolean).join(" · ")}</Typography.Text>
      </div>

      <Section title="客户资料">
        <Descriptions size="small" column={1} items={[
          { key: "country", label: "国家/地区", children: customer.country || "—" },
          { key: "email", label: "邮箱", children: customer.email || "—" },
          { key: "phone", label: "电话", children: [customer.mobile, customer.phone].filter(Boolean).join(" / ") || "—" },
          { key: "register", label: "注册时间", children: customer.registerDate || "—" },
          { key: "sessions", label: "本页会话", children: `${group.sessionIds.length} 个 · 未读 ${group.unread} · 待回复 ${group.needsReply}` },
        ]} />
      </Section>

      <Section title="平台资料">
        <div className="flex flex-wrap items-center gap-1">
          {customer.tags.length ? customer.tags.map((tag) => <Tag key={tag} className="!m-0 !text-[11px]">{tag}</Tag>) : <Typography.Text type="secondary" className="!text-xs">无</Typography.Text>}
        </div>
        <Typography.Text type="secondary" className="!text-xs">品质：{customer.qualityTag || "—"} · 成长层级：{customer.growthLevel || "—"}</Typography.Text>
      </Section>

      <Section title="客户群像">
        <Descriptions size="small" column={1} items={[
          { key: "stage", label: "业务阶段", children: <Tag color={portraitStageColor(customer.businessStage)} className="!m-0">{portraitStageLabel(customer.businessStage)}</Tag> },
          {
            key: "tags", label: "业务标签",
            children: customer.businessTags.length
              ? <div className="flex flex-wrap gap-1">{customer.businessTags.map((tag) => <Tag key={tag.tag_id} className="!m-0 !text-[11px]">{portraitTagLabel(tag)}</Tag>)}</div>
              : "—",
          },
          {
            key: "lost", label: "流失信息",
            children: customer.businessStage === "lost" && customer.lostReason
              ? <span><Tag color="red" className="!m-0">{lostReasonLabel(customer.lostReason)}</Tag>{customer.lostNote ? <div className="mt-1 text-xs text-slate-500">{customer.lostNote}</div> : null}</span>
              : "—",
          },
          {
            key: "version", label: "版本/更新",
            children: customer.businessVersion
              ? `v${customer.businessVersion}${customer.lostAt ? ` · ${formatDateTime(customer.lostAt)}` : ""}`
              : "未维护",
          },
        ]} />
      </Section>

      <Section title={`包含会话（${group.sessionIds.length}）`}>
        <SessionRows conversations={group.conversations} />
      </Section>

      <Section title="操作历史">
        {operations.length ? (
          <div className="flex flex-col gap-1">
            {operations.map((entry) => (
              <Typography.Text key={entry.request_id} type="secondary" className="!text-xs">
                {formatDateTime(entry.created_at)} · {operationSummary(entry, portraitTagOptions(portraitTags))}
              </Typography.Text>
            ))}
          </div>
        ) : (
          <Typography.Text type="secondary" className="!text-xs">暂无操作历史</Typography.Text>
        )}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Typography.Text strong className="!text-[13px]">{title}</Typography.Text>
      {children}
    </div>
  );
}
