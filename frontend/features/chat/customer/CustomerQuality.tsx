"use client";

import { Card, Tag } from "antd";
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
  const rows = [
    [
      { label: "浏览商品", value: behavior?.productViews },
      { label: "有效询盘", value: behavior?.validInquiries },
      { label: "有效 RFQ", value: behavior?.validRfqs },
    ],
    [
      { label: "登录天数", value: behavior?.loginDays },
      { label: "垃圾询盘", value: behavior?.spamInquiries },
      { label: "被拉黑次数", value: behavior?.blacklisted },
    ],
  ];
  return (
    <Card title="客户质量指数" size="small">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <CustomerQualityBadge behavior={behavior} gradeOnly={quality !== null} />
          {quality !== null ? <span className="text-xl font-semibold tabular-nums">{quality.score} <span className="text-sm font-normal text-slate-400">/ 100</span></span> : null}
        </div>
        <table className="w-full table-fixed border-collapse text-center">
          <caption className="pb-2 text-left text-xs text-slate-500">近 90 天行为</caption>
          <tbody>
            {rows.map((row) => (
              <tr key={row[0].label} className="border-t border-slate-100">
                {row.map((metric) => (
                  <td key={metric.label} className="px-1 py-2 align-top">
                    <div className="text-xs text-slate-500">{metric.label}</div>
                    <div className="mt-1 text-sm font-medium tabular-nums">{value(metric.value)}</div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-slate-500">已回复询盘：{value(behavior?.repliedInquiries)}</p>
      </div>
    </Card>
  );
}
