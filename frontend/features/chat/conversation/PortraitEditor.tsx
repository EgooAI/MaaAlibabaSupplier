"use client";

import { App, Button, Card, Input, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import {
  LOST_REASON_OPTIONS,
  PORTRAIT_STAGE_OPTIONS,
  operationSummary,
  portraitRequestId,
  portraitTagLabel,
} from "@/domain/portrait/portraitModel";
import { formatDateTime } from "@/domain/time";
import { ApiError } from "@/services/httpAdapter";
import type { ConversationDetail } from "@/types/chatCanonical";
import type {
  LostReason,
  PortraitChangesInput,
  PortraitOperationEntry,
  PortraitStage,
  PortraitTag,
} from "@/types/portrait";

export function PortraitEditor({ conversation, tags, onTagCreated, onSaved }: {
  conversation: ConversationDetail;
  tags: PortraitTag[];
  onTagCreated?: (tag: PortraitTag) => void;
  onSaved?: () => void;
}) {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const { customer } = conversation;
  const editable = Boolean(customer.portraitEditable && customer.cid);
  const version = customer.businessVersion;
  const [stage, setStage] = useState<PortraitStage>(customer.businessStage);
  const [selectedTags, setSelectedTags] = useState<string[]>(() => customer.businessTags.map((tag) => tag.tag_id));
  const [lostReason, setLostReason] = useState<LostReason | undefined>(customer.lostReason ?? undefined);
  const [lostNote, setLostNote] = useState(customer.lostNote ?? "");
  const [newTagName, setNewTagName] = useState("");
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [operations, setOperations] = useState<PortraitOperationEntry[]>([]);
  // Reset drafts when the displayed portrait version changes (render-time
  // adjustment instead of an effect; see "adjusting state when props change").
  const syncKey = `${conversation.id}:${version}`;
  const [syncedKey, setSyncedKey] = useState(syncKey);
  if (syncedKey !== syncKey) {
    setSyncedKey(syncKey);
    setStage(customer.businessStage);
    setSelectedTags(customer.businessTags.map((tag) => tag.tag_id));
    setLostReason(customer.lostReason ?? undefined);
    setLostNote(customer.lostNote ?? "");
    setNewTagName("");
    setOperations([]);
  }

  useEffect(() => {
    if (!editable || !customer.cid) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await backend.listPortraitOperations(customer.cid!);
        if (!cancelled) setOperations(result.operations);
      } catch {
        // History is informational.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [backend, editable, customer.cid, version]);

  const tagOptions = useMemo(() => {
    const selected = new Set(selectedTags);
    const options = new Map<string, { value: string; label: string; disabled?: boolean }>();
    for (const tag of tags) {
      options.set(tag.tag_id, {
        value: tag.tag_id,
        label: portraitTagLabel(tag),
        disabled: tag.archived && !selected.has(tag.tag_id),
      });
    }
    for (const tag of customer.businessTags) {
      if (!options.has(tag.tag_id)) options.set(tag.tag_id, { value: tag.tag_id, label: tag.name });
    }
    return [...options.values()];
  }, [tags, customer.businessTags, selectedTags]);

  async function createTag() {
    const name = newTagName.trim();
    if (!name || creating) return;
    setCreating(true);
    try {
      const result = await backend.createPortraitTag({ name });
      onTagCreated?.(result.tag);
      setSelectedTags((current) => (current.includes(result.tag.tag_id) ? current : [...current, result.tag.tag_id]));
      setNewTagName("");
      message.success("标签已创建并选中，保存后生效");
    } catch (error) {
      if (!(error instanceof AccountChangedError)) message.error(error instanceof Error ? error.message : "标签创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function save() {
    if (!editable || saving || !customer.cid) return;
    const currentTags = customer.businessTags.map((tag) => tag.tag_id);
    const changes: PortraitChangesInput = {};
    if (stage !== customer.businessStage) changes.stage = stage;
    const add = selectedTags.filter((tagId) => !currentTags.includes(tagId));
    const remove = currentTags.filter((tagId) => !selectedTags.includes(tagId));
    if (add.length) changes.add_tag_ids = add;
    if (remove.length) changes.remove_tag_ids = remove;
    if (stage === "lost") {
      const reason = lostReason ?? "unclear";
      if (reason !== customer.lostReason) changes.lost_reason = reason;
      const note = lostNote.trim();
      if (note !== (customer.lostNote ?? "")) changes.lost_note = note;
    }
    if (!Object.keys(changes).length) {
      message.info("没有需要保存的修改");
      return;
    }
    setSaving(true);
    try {
      await backend.savePortraitBatch({
        request_id: portraitRequestId(),
        targets: [{
          sid: Number(conversation.id),
          expected_cid: customer.cid,
          expected_version: version,
        }],
        changes,
      });
      message.success("客户画像已保存");
      onSaved?.();
    } catch (error) {
      if (!(error instanceof AccountChangedError)) {
        message.error(error instanceof Error ? error.message : "画像保存失败");
        // A conflict means the displayed version is stale; re-read before retrying.
        if (error instanceof ApiError && error.status === 409) onSaved?.();
      }
    } finally {
      setSaving(false);
    }
  }

  if (!editable) {
    return (
      <Card title="客户经营画像" size="small">
        <Typography.Text type="secondary">
          客户身份无法确定，暂不能维护阶段和标签；聊天与导出不受影响。
        </Typography.Text>
      </Card>
    );
  }

  return (
    <Card title="客户经营画像" size="small">
      <Space orientation="vertical" className="w-full" size="middle">
        <div className="flex flex-wrap items-center gap-2">
          <Typography.Text type="secondary">阶段</Typography.Text>
          <Select
            aria-label="客户阶段"
            size="small"
            value={stage}
            onChange={setStage}
            options={PORTRAIT_STAGE_OPTIONS}
            className="min-w-[140px]"
          />
          {stage === "lost" ? (
            <Select
              aria-label="失败原因"
              size="small"
              value={lostReason ?? "unclear"}
              onChange={setLostReason}
              options={LOST_REASON_OPTIONS}
              className="min-w-[120px]"
            />
          ) : null}
        </div>
        {stage === "lost" ? (
          <Input
            aria-label="失败备注"
            size="small"
            maxLength={500}
            placeholder="失败备注（可选）"
            value={lostNote}
            onChange={(event) => setLostNote(event.target.value)}
          />
        ) : null}
        <div>
          <Typography.Text type="secondary">业务标签</Typography.Text>
          <Select
            aria-label="业务标签"
            mode="multiple"
            className="mt-1 w-full"
            placeholder="选择业务标签"
            value={selectedTags}
            onChange={setSelectedTags}
            options={tagOptions}
            optionFilterProp="label"
          />
        </div>
        <Space.Compact className="w-full">
          <Input
            aria-label="新标签名称"
            size="small"
            maxLength={40}
            placeholder="新标签名称"
            value={newTagName}
            onChange={(event) => setNewTagName(event.target.value)}
            onPressEnter={() => void createTag()}
          />
          <Button size="small" loading={creating} onClick={() => void createTag()}>创建并选中</Button>
        </Space.Compact>
        <Button type="primary" size="small" loading={saving} onClick={() => void save()}>保存画像</Button>
        {operations.length ? (
          <div>
            <Typography.Text type="secondary">最近变更</Typography.Text>
            <div className="mt-1 flex flex-col gap-1">
              {operations.slice(0, 5).map((entry) => (
                <Typography.Text key={entry.request_id} className="text-xs" type="secondary">
                  {formatDateTime(entry.created_at)} · {operationSummary(entry, tagOptions)}
                </Typography.Text>
              ))}
            </div>
          </div>
        ) : null}
      </Space>
    </Card>
  );
}
