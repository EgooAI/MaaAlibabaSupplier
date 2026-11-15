"use client";

import { DownOutlined } from "@ant-design/icons";
import { App, Button, Input, Popover, Select, Typography } from "antd";
import { useState } from "react";
import { PORTRAIT_LOST_NOTE_MAX } from "@/domain/contract.generated";
import { LOST_REASON_OPTIONS, PORTRAIT_STAGE_OPTIONS, portraitRequestId, portraitStageLabel } from "@/domain/portrait/portraitModel";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { ApiError } from "@/services/httpAdapter";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { LostReason, PortraitChangesInput, PortraitStage } from "@/types/portrait";

type CustomerStageProps = { conversation: ConversationDetail; onSaved: () => void };

export function CustomerStageControl(props: CustomerStageProps) {
  const { id, customer } = props.conversation;
  return <StageEditor key={`${id}:${customer.cid}:${customer.businessVersion}`} {...props} />;
}

function StageEditor({ conversation, onSaved }: CustomerStageProps) {
  const { message } = App.useApp();
  const { blocked } = useAccount();
  const backend = useAccountBackend();
  const { customer } = conversation;
  const disabled = blocked || !customer.portraitEditable || !customer.cid;
  const [open, setOpen] = useState(false);
  const [stage, setStage] = useState<PortraitStage>(customer.businessStage);
  const [lostReason, setLostReason] = useState<LostReason>(customer.lostReason ?? "unclear");
  const [lostNote, setLostNote] = useState(customer.lostNote ?? "");
  const [saving, setSaving] = useState(false);
  const changes: PortraitChangesInput = {};
  if (stage !== customer.businessStage) changes.stage = stage;
  if (stage === "lost") {
    if (lostReason !== customer.lostReason) changes.lost_reason = lostReason;
    if (lostNote.trim() !== (customer.lostNote ?? "")) changes.lost_note = lostNote.trim();
  }

  function changeOpen(next: boolean) {
    if (saving) return;
    if (next) {
      setStage(customer.businessStage);
      setLostReason(customer.lostReason ?? "unclear");
      setLostNote(customer.lostNote ?? "");
    }
    setOpen(next);
  }

  async function save() {
    if (disabled || saving || !customer.cid || !Object.keys(changes).length) return;
    setSaving(true);
    try {
      await backend.savePortraitBatch({
        request_id: portraitRequestId(),
        targets: [{ sid: Number(conversation.id), expected_cid: customer.cid, expected_version: customer.businessVersion }],
        changes,
      });
      message.success("客户阶段已保存");
      setOpen(false);
      onSaved();
    } catch (error) {
      if (!(error instanceof AccountChangedError)) {
        message.error(error instanceof Error ? error.message : "阶段保存失败");
        if (error instanceof ApiError && error.status === 409) {
          setOpen(false);
          onSaved();
        }
      }
    } finally {
      setSaving(false);
    }
  }

  return <Popover title="客户阶段" trigger="click" placement="bottomRight" destroyOnHidden
    open={open && !disabled} onOpenChange={changeOpen}
    content={<div className="flex w-64 max-w-[calc(100vw-48px)] flex-col gap-3">
      <Select aria-label="客户阶段" value={stage} onChange={setStage} options={PORTRAIT_STAGE_OPTIONS} disabled={saving} />
      {stage === "lost" ? <>
        <div className="flex flex-col gap-1">
          <Typography.Text type="secondary">失败原因</Typography.Text>
          <Select aria-label="失败原因" value={lostReason} onChange={setLostReason} options={LOST_REASON_OPTIONS} disabled={saving} />
        </div>
        <Input.TextArea aria-label="失败备注" placeholder="失败备注（可选）" rows={2} maxLength={PORTRAIT_LOST_NOTE_MAX}
          value={lostNote} onChange={(event) => setLostNote(event.target.value)} disabled={saving} />
      </> : null}
      <div className="flex justify-end gap-2">
        <Button size="small" disabled={saving} onClick={() => changeOpen(false)}>取消</Button>
        <Button type="primary" size="small" loading={saving} disabled={!Object.keys(changes).length} onClick={() => void save()}>保存阶段</Button>
      </div>
    </div>}>
    <Button type="text" size="small" className="!text-xs" disabled={disabled} aria-label={`客户阶段：${portraitStageLabel(customer.businessStage)}`}>
      {portraitStageLabel(customer.businessStage)}<DownOutlined />
    </Button>
  </Popover>;
}
