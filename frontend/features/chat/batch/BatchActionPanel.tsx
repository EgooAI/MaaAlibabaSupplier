"use client";

import { CloseOutlined } from "@ant-design/icons";
import { Button, Card, Checkbox, Input, Segmented, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { LOST_REASON_OPTIONS, PORTRAIT_STAGE_OPTIONS, lostReasonLabel, portraitStageColor, portraitStageLabel } from "@/domain/portrait/portraitModel";
import { portraitChangesError, previewPortraitChanges } from "@/domain/portrait/batchPreview";
import type { BatchPanelAction } from "@/domain/portrait/batchPreview";
import type { Conversation } from "@/types/chatCanonical";
import type { LostReason, PortraitChangesInput, PortraitStage, PortraitTag } from "@/types/portrait";

/**
 * 批量画像配置面板：表单 → 影响预览 → 应用（面板即确认，提交行为保持“消息 + 自动清空 + 重载”）。
 * 通过父级 key 在动作切换 / 重新打开时重置表单。
 */
export function BatchActionPanel({ action, targets, portraitTags, busy, onActionChange, onClose, onApply }: {
  action: BatchPanelAction;
  targets: Conversation[];
  portraitTags: PortraitTag[];
  busy: boolean;
  onActionChange: (action: BatchPanelAction) => void;
  onClose: () => void;
  onApply: (changes: PortraitChangesInput, description: string) => void;
}) {
  const [stage, setStage] = useState<PortraitStage | null>(null);
  const [lostReason, setLostReason] = useState<LostReason | null>(null);
  const [lostNote, setLostNote] = useState("");
  const [tagIds, setTagIds] = useState<string[]>([]);

  const changes = useMemo<PortraitChangesInput>(() => {
    if (action === "stage") {
      const result: PortraitChangesInput = {};
      if (stage) result.stage = stage;
      if (stage === "lost") {
        if (lostReason) result.lost_reason = lostReason;
        const note = lostNote.trim();
        if (note) result.lost_note = note;
      }
      return result;
    }
    if (action === "add-tag") return tagIds.length ? { add_tag_ids: tagIds } : {};
    return tagIds.length ? { remove_tag_ids: tagIds } : {};
  }, [action, stage, lostReason, lostNote, tagIds]);

  const error = portraitChangesError(changes, targets, portraitTags);
  const preview = useMemo(() => previewPortraitChanges(changes, targets, portraitTags), [changes, targets, portraitTags]);
  const changed = preview.filter((row) => row.status === "change").length;
  const unchanged = preview.filter((row) => row.status === "unchanged").length;
  const unavailable = preview.filter((row) => row.status === "unavailable").length;
  const resolvable = targets.filter((conversation) => conversation.customer.cid !== null);
  const customerCount = new Set(resolvable.map((conversation) => conversation.customer.cid)).size;
  const leavingLost = stage && stage !== "lost" ? resolvable.filter((conversation) => conversation.customer.businessStage === "lost").length : 0;

  const held = useMemo(() => {
    const map = new Map<string, { tag_id: string; name: string; archived: boolean; count: number }>();
    for (const conversation of targets) {
      if (conversation.customer.cid === null) continue;
      for (const tag of conversation.customer.businessTags) {
        const entry = map.get(tag.tag_id) ?? { tag_id: tag.tag_id, name: tag.name, archived: Boolean(tag.archived), count: 0 };
        entry.count += 1;
        map.set(tag.tag_id, entry);
      }
    }
    return [...map.values()];
  }, [targets]);

  function tagNameOf(tagId: string) {
    return portraitTags.find((tag) => tag.tag_id === tagId)?.name ?? tagId;
  }

  function describeChanges() {
    if (action === "stage" && stage) {
      if (stage === "lost" && lostReason) return `阶段设为「流失/失败」（原因：${lostReasonLabel(lostReason)}）`;
      return `阶段设为「${portraitStageLabel(stage)}」`;
    }
    if (action === "add-tag") return `增加标签「${tagIds.map(tagNameOf).join("、")}」`;
    if (action === "remove-tag") return `移除标签「${tagIds.map(tagNameOf).join("、")}」`;
    return "画像变更";
  }

  return (
    <Card
      size="small"
      title="批量操作"
      extra={<Button type="text" size="small" aria-label="关闭面板" icon={<CloseOutlined />} onClick={onClose} />}
    >
      <div className="flex flex-col gap-3">
        <Segmented
          block
          value={action}
          onChange={(value) => onActionChange(value as BatchPanelAction)}
          options={[{ value: "stage", label: "设置阶段" }, { value: "add-tag", label: "加标签" }, { value: "remove-tag", label: "移除标签" }]}
        />

        {action === "stage" ? (
          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-2 gap-1.5">
              {PORTRAIT_STAGE_OPTIONS.map((option) => {
                const count = resolvable.filter((conversation) => conversation.customer.businessStage === option.value).length;
                const selected = stage === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setStage(option.value)}
                    className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-left ${selected ? "border-blue-400 bg-blue-50" : "border-slate-200 hover:bg-slate-50"}`}
                  >
                    <Tag color={portraitStageColor(option.value)} className="!m-0 !text-[11px]">{option.label}</Tag>
                    {count ? <span className="text-[10px] text-slate-400">{count} 位已是</span> : null}
                  </button>
                );
              })}
            </div>
            {leavingLost ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-700">
                {leavingLost} 位客户当前为流失，提交后将清除失败信息（历史保留）。
              </div>
            ) : null}
            {stage === "lost" ? (
              <div className="flex flex-col gap-2 rounded-md border border-slate-100 p-2">
                <Typography.Text type="secondary" className="!text-xs">失败原因</Typography.Text>
                <div className="flex flex-wrap gap-1">
                  {LOST_REASON_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => setLostReason(option.value)}
                      className={`rounded-full border px-2.5 py-0.5 text-[11px] ${lostReason === option.value ? "border-red-300 bg-red-50 font-medium text-red-700" : "border-slate-200 text-slate-600 hover:bg-slate-50"}`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <Input.TextArea
                  aria-label="失败备注"
                  rows={2}
                  maxLength={500}
                  placeholder="失败备注（可选）"
                  value={lostNote}
                  onChange={(event) => setLostNote(event.target.value)}
                />
                <div className="text-right text-[10px] text-slate-400">{lostNote.length} / 500</div>
              </div>
            ) : null}
          </div>
        ) : null}

        {action === "add-tag" ? (
          <div className="flex max-h-[220px] flex-col gap-1 overflow-auto rounded-md border border-slate-100 p-2">
            {portraitTags.length ? portraitTags.map((tag) => (
              <Checkbox
                key={tag.tag_id}
                disabled={tag.archived}
                checked={tagIds.includes(tag.tag_id)}
                onChange={(event) => setTagIds((current) => event.target.checked ? [...current, tag.tag_id] : current.filter((id) => id !== tag.tag_id))}
              >
                <span className="text-xs">{tag.name}</span>
                {tag.archived ? <span className="ml-1 text-[11px] text-slate-400">（已停用，不可新增）</span> : null}
              </Checkbox>
            )) : <Typography.Text type="secondary" className="!text-xs">标签目录为空</Typography.Text>}
          </div>
        ) : null}

        {action === "remove-tag" ? (
          <div className="flex max-h-[220px] flex-col gap-1 overflow-auto rounded-md border border-slate-100 p-2">
            {held.length ? held.map((tag) => (
              <Checkbox
                key={tag.tag_id}
                checked={tagIds.includes(tag.tag_id)}
                onChange={(event) => setTagIds((current) => event.target.checked ? [...current, tag.tag_id] : current.filter((id) => id !== tag.tag_id))}
              >
                <span className="text-xs">{tag.name}</span>
                {tag.archived ? <span className="ml-1 text-[11px] text-slate-400">（已停用）</span> : null}
                <span className="ml-1 text-[11px] text-slate-400">{tag.count} 位客户持有</span>
              </Checkbox>
            )) : <Typography.Text type="secondary" className="!text-xs">所选客户当前没有可移除的标签</Typography.Text>}
          </div>
        ) : null}

        <div className="border-t border-slate-100 pt-3">
          <Typography.Text strong className="!text-[13px]">影响预览</Typography.Text>
          <div className="mt-2 grid grid-cols-3 gap-1.5">
            <Tile n={changed} label="将变更" tone="blue" />
            <Tile n={unchanged} label="无变化" tone="slate" />
            <Tile n={unavailable} label="不可维护" tone="muted" />
          </div>
          <div className="mt-2 max-h-[240px] overflow-auto rounded-md border border-slate-100">
            {preview.length ? preview.map((row) => (
              <div key={row.conversation.id} className="flex flex-col gap-0.5 border-b border-slate-50 px-2.5 py-1.5 last:border-b-0">
                <span className="truncate text-xs font-medium">{row.conversation.customer.name}</span>
                {row.lines.length
                  ? row.lines.map((line) => <span key={line} className="text-[11px] text-slate-500">{line}</span>)
                  : <span className="text-[11px] text-slate-400">{row.note}</span>}
              </div>
            )) : <div className="px-2.5 py-3 text-center text-[11px] text-slate-400">选择目标与变更后显示差异</div>}
          </div>
        </div>

        <div className="flex flex-col gap-1.5 border-t border-slate-100 pt-3">
          <Button
            type="primary"
            block
            loading={busy}
            disabled={busy || Boolean(error) || !targets.length}
            onClick={() => onApply(changes, describeChanges())}
          >
            应用到 {customerCount} 位客户（{resolvable.length} 个会话）
          </Button>
          <div className="text-center text-[11px] text-slate-400">
            {error ?? "request_id 幂等 · 无变化不产生版本"}
          </div>
        </div>
      </div>
    </Card>
  );
}

function Tile({ n, label, tone }: { n: number; label: string; tone: "blue" | "slate" | "muted" }) {
  const toneClass = tone === "blue" ? "text-blue-600" : tone === "slate" ? "text-slate-500" : "text-slate-300";
  return (
    <div className="rounded-md border border-slate-100 px-2 py-1">
      <div className={`text-sm font-semibold ${toneClass}`}>{n}</div>
      <div className="text-[10px] text-slate-400">{label}</div>
    </div>
  );
}
