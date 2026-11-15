"use client";

import { SaveOutlined } from "@ant-design/icons";
import { App, Button, Card, Input, Select, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { PORTRAIT_TAG_NAME_MAX } from "@/domain/contract.generated";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import {
  operationSummary,
  portraitRequestId,
  portraitTagLabel,
} from "@/domain/portrait/portraitModel";
import { formatDateTime } from "@/domain/time";
import { ApiError } from "@/services/httpAdapter";
import { TagManagerModal } from "@/features/chat/portrait/TagManagerModal";
import type { ConversationDetail } from "@/types/chatCanonical";
import type {
  PortraitChangesInput,
  PortraitOperationEntry,
  PortraitTag,
} from "@/types/portrait";

export function PortraitEditor({ conversation, tags, onTagCreated, onSaved, onTagsChanged }: {
  conversation: ConversationDetail;
  tags: PortraitTag[];
  onTagCreated?: (tag: PortraitTag) => void;
  onSaved?: () => void;
  onTagsChanged?: () => void;
}) {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const { customer } = conversation;
  const editable = Boolean(customer.portraitEditable && customer.cid);
  const version = customer.businessVersion;
  const [selectedTags, setSelectedTags] = useState<string[]>(() => customer.businessTags.map((tag) => tag.tag_id));
  const [newTagName, setNewTagName] = useState("");
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [operations, setOperations] = useState<PortraitOperationEntry[]>([]);
  // Reset drafts when the displayed portrait version changes (render-time
  // adjustment instead of an effect; see "adjusting state when props change").
  const syncKey = `${conversation.id}:${version}`;
  const [syncedKey, setSyncedKey] = useState(syncKey);
  if (syncedKey !== syncKey) {
    setSyncedKey(syncKey);
    setSelectedTags(customer.businessTags.map((tag) => tag.tag_id));
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
    const add = selectedTags.filter((tagId) => !currentTags.includes(tagId));
    const remove = currentTags.filter((tagId) => !selectedTags.includes(tagId));
    if (add.length) changes.add_tag_ids = add;
    if (remove.length) changes.remove_tag_ids = remove;
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
      message.success("画像标签已保存");
      onSaved?.();
    } catch (error) {
      if (!(error instanceof AccountChangedError)) {
        message.error(error instanceof Error ? error.message : "标签保存失败");
        // A conflict means the displayed version is stale; re-read before retrying.
        if (error instanceof ApiError && error.status === 409) onSaved?.();
      }
    } finally {
      setSaving(false);
    }
  }

  if (!editable) {
    return (
      <Card title="画像标签">
        <Typography.Text type="secondary">
          客户身份无法确定，暂不能维护标签。
        </Typography.Text>
      </Card>
    );
  }

  return (
    <Card
      title="画像标签"
      extra={<Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>保存标签</Button>}
    >
      <div className="flex flex-col gap-2">
        <Select
          aria-label="画像标签"
          mode="multiple"
          className="w-full"
          placeholder="选择画像标签"
          value={selectedTags}
          onChange={setSelectedTags}
          options={tagOptions}
          optionFilterProp="label"
        />
        <div className="flex items-center justify-between gap-2">
          <Typography.Text type="secondary" className="!text-xs">保存后生效</Typography.Text>
          <Button size="small" type="text" onClick={() => setManageOpen(true)}>管理标签</Button>
        </div>
        <details key={syncKey}>
          <summary className="cursor-pointer text-xs text-slate-500">新建标签</summary>
          <Space.Compact className="mt-2 w-full">
            <Input
              aria-label="新标签名称"
              maxLength={PORTRAIT_TAG_NAME_MAX}
              placeholder="新标签名称"
              value={newTagName}
              onChange={(event) => setNewTagName(event.target.value)}
              onPressEnter={() => void createTag()}
            />
            <Button loading={creating} onClick={() => void createTag()}>创建并选中</Button>
          </Space.Compact>
        </details>
      </div>
      <TagManagerModal
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        tags={tags}
        onChanged={onTagsChanged}
      />
      {operations.length ? (
        <details key={syncKey} className="mt-4 border-t border-slate-100 pt-3">
          <summary className="cursor-pointer text-xs text-slate-500">最近变更</summary>
          <div className="mt-2 flex flex-col gap-1">
            {operations.slice(0, 5).map((entry) => (
              <Typography.Text key={entry.request_id} className="!text-xs" type="secondary">
                {formatDateTime(entry.created_at)} · {operationSummary(entry, tagOptions)}
              </Typography.Text>
            ))}
          </div>
        </details>
      ) : null}
    </Card>
  );
}
