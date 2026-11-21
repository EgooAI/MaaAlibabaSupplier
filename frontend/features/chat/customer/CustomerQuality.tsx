"use client";

import { Card, Descriptions, Tag } from "antd";
import type { CSSProperties } from "react";
import { customerQualityOf, type CustomerQualityGrade } from "@/domain/chat/customerQuality";
import type { CustomerD90Behavior } from "@/types/chatCanonical";

const gradeStyles: Record<CustomerQualityGrade, CSSProperties> = {
  S: { color: "#b91c1c", backgroundColor: "#fef2f2", borderColor: "#fecaca" },
  A: { color: "#a15c00", backgroundColor: "#fff7e6", borderColor: "#ffd591" },
  B: { color: "#6d28d9", backgroundColor: "#f5f3ff", borderColor: "#ddd6fe" },
  C: { color: "#1d4ed8", backgroundColor: "#eff6ff", borderColor: "#bfdbfe" },
  D: { color: "#52675b", backgroundColor: "#eef3ef", borderColor: "#c9d6cc" },
};

export function CustomerQualityBadge({ behavior, gradeOnly = false }: {
  behavior?: CustomerD90Behavior;
  gradeOnly?: boolean;
}) {
  const quality = customerQualityOf(behavior);
  const label = quality
    ? `客户质量指数：${quality.grade}${gradeOnly ? "" : ` · ${quality.score} 分`}`
    : "客户质量指数：数据不足";
  return (
    <Tag
      className="!m-0 shrink-0 !text-xs"
      style={quality ? gradeStyles[quality.grade] : undefined}
      title={label}
      aria-label={label}
    >
      {quality ? (gradeOnly ? quality.grade : `${quality.grade} · ${quality.score}`) : gradeOnly ? "—" : "数据不足"}
    </Tag>
  );
}

export function CustomerQualityDetails({ behavior }: { behavior?: CustomerD90Behavior }) {
  const quality = customerQualityOf(behavior);
  const value = (number: number | undefined) => number ?? "—";
  return (
    <Card title="客户质量指数" size="small">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <CustomerQualityBadge behavior={behavior} gradeOnly={quality !== null} />
          {quality !== null ? <span className="text-xl font-semibold tabular-nums">{quality.score} <span className="text-sm font-normal text-slate-400">/ 100</span></span> : null}
        </div>
        <p className="text-xs text-slate-500">根据已采集行为数据计算，缺失项按 0 计入。六项均无正值时显示“数据不足”。</p>
        <Descriptions title="近 90 天行为" size="small" column={1} items={[
          { key: "a", label: "A · 浏览", children: value(behavior?.productViews) },
          { key: "b", label: "B · 有效询盘", children: value(behavior?.validInquiries) },
          { key: "c", label: "C · 有效 RFQ", children: value(behavior?.validRfqs) },
          { key: "d", label: "D · 登录天数", children: value(behavior?.loginDays) },
          { key: "e", label: "E · 垃圾询盘", children: value(behavior?.spamInquiries) },
          { key: "f", label: "F · 拉黑", children: value(behavior?.blacklisted) },
          { key: "replied", label: "已回复询盘", children: value(behavior?.repliedInquiries) },
        ]} />
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">计算与评级说明</summary>
          <div className="mt-2 space-y-2">
            <p className="break-words">I′ = round(clamp(2 × log₂(A + 2D + 4B + 8C + 1) × [1 + (4B + 8C + 1) / (A + 2D + 1)] − 8 × log₂(E + F + 1) + 60, 0, 100))</p>
            <p>先将分数限制在 0–100，再四舍五入取整。已回复询盘不参与计分。</p>
            <p>S：90–100，A：80–89，B：70–79，C：60–69，D：0–59。</p>
            <p>行为数据为采集时保存的值，尚无独立采集时间，可能未反映最新变化。</p>
          </div>
        </details>
      </div>
    </Card>
  );
}
