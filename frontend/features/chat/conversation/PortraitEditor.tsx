"use client";

import { SaveOutlined } from "@ant-design/icons";
import { App, Button, Card, Select, Typography } from "antd";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { PORTRAIT_TAG_NAME_MAX } from "@/domain/contract.generated";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import {
  operationSummary,
  portraitRequestId,
  portraitTagLabel,
} from "@/domain/portrait/portraitModel";
import { formatDateTime } from "@/domain/time";
import { ApiError, PortraitTagConflict } from "@/services/httpAdapter";
import { TagManagerModal } from "@/features/chat/portrait/TagManagerModal";
import type { ConversationDetail } from "@/types/chatCanonical";
import type {
  PortraitChangesInput,
  PortraitOperationEntry,
  PortraitTag,
} from "@/types/portrait";

type PortraitEditorProps = {
  conversation: ConversationDetail;
  tags: PortraitTag[];
  onTagCreated?: (tag: PortraitTag) => void;
  onSaved?: () => void;
  onTagsChanged?: () => void;
};

export function PortraitEditor(props: PortraitEditorProps) {
  const { id, customer } = props.conversation;
  return <TagEditor key={`${id}:${customer.cid}:${customer.businessVersion}`} {...props} />;
}

function TagEditor({ conversation, tags, onTagCreated, onSaved, onTagsChanged }: PortraitEditorProps) {
  const { message } = App.useApp();
  const { blocked } = useAccount();
  const backend = useAccountBackend();
  const { customer } = conversation;
  const editable = Boolean(customer.portraitEditable && customer.cid);
  const version = customer.businessVersion;
  const [selectedTags, setSelectedTags] = useState<string[]>(() => customer.businessTags.map((tag) => tag.tag_id));
  const [searchValue, setSearchValue] = useState("");
  const [createdTags, setCreatedTags] = useState<PortraitTag[]>([]);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [operations, setOperations] = useState<PortraitOperationEntry[]>([]);

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

  const catalog = useMemo(() => {
    const byId = new Map<string, PortraitTag>();
    for (const tag of [...createdTags, ...tags]) {
      const current = byId.get(tag.tag_id);
      if (!current || tag.version >= current.version) byId.set(tag.tag_id, tag);
    }
    return [...byId.values()];
  }, [createdTags, tags]);
  const catalogRef = useRef(catalog);
  useLayoutEffect(() => { catalogRef.current = catalog; }, [catalog]);

  const tagOptions = useMemo(() => {
    const selected = new Set(selectedTags);
    const options = new Map<string, { value: string; label: string; disabled?: boolean }>();
    for (const tag of [...customer.businessTags, ...catalog]) {
      options.set(tag.tag_id, {
        value: tag.tag_id,
        label: portraitTagLabel(tag),
        disabled: tag.archived && !selected.has(tag.tag_id),
      });
    }
    return [...options.values()];
  }, [catalog, customer.businessTags, selectedTags]);

  async function selectTag(input: string) {
    if (!editable || blocked || creating || saving) return;
    const name = input.normalize("NFKC").trim();
    if (!name) return;
    const existing = tagOptions.find((tag) => tag.value === input);
    if (existing) {
      if (existing.disabled) {
        message.warning("该标签已停用，不能新增关联");
        return;
      }
      setSearchValue("");
      setSelectedTags((current) => current.includes(existing.value) ? current : [...current, existing.value]);
      return;
    }
    if ([...name].length > PORTRAIT_TAG_NAME_MAX) {
      message.warning(`标签名称不能超过 ${PORTRAIT_TAG_NAME_MAX} 个字符`);
      return;
    }
    setSearchValue("");
    setCreating(true);
    try {
      let tag: PortraitTag;
      try {
        ({ tag } = await backend.createPortraitTag({ name }));
      } catch (error) {
        if (!(error instanceof PortraitTagConflict)) throw error;
        tag = error.tag;
      }
      const current = catalogRef.current.find((item) => item.tag_id === tag.tag_id);
      if (current && current.version >= tag.version) tag = current;
      setCreatedTags((current) => [...current.filter((item) => item.tag_id !== tag.tag_id), tag]);
      onTagCreated?.(tag);
      if (tag.archived && !selectedTags.includes(tag.tag_id)) {
        message.warning("该标签已停用，不能新增关联");
        return;
      }
      setSelectedTags((current) => current.includes(tag.tag_id) ? current : [...current, tag.tag_id]);
      message.success("标签已选中，保存后生效");
    } catch (error) {
      if (!(error instanceof AccountChangedError)) {
        setSearchValue(name);
        message.error(error instanceof Error ? error.message : "标签创建失败");
      }
    } finally {
      setCreating(false);
    }
  }

  async function save() {
    if (!editable || blocked || saving || creating || !customer.cid) return;
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
      extra={<Button type="primary" size="small" icon={<SaveOutlined />} loading={saving} disabled={blocked || creating} onClick={() => void save()}>保存标签</Button>}
    >
      <div className="flex flex-col gap-2">
        <Select
          aria-label="画像标签"
          mode="tags"
          className="w-full"
          placeholder="选择标签或输入新标签"
          value={selectedTags}
          onSelect={(value) => void selectTag(value)}
          onDeselect={(value) => setSelectedTags((current) => current.filter((tagId) => tagId !== value))}
          options={tagOptions.filter((tag) => !tag.disabled)}
          showSearch={{ optionFilterProp: "label", searchValue, onSearch: setSearchValue }}
          loading={creating}
          disabled={blocked || creating || saving}
        />
        <div className="flex items-center justify-between gap-2">
          <Typography.Text type="secondary" className="!text-xs">回车添加，保存后生效</Typography.Text>
          <Button size="small" type="text" disabled={blocked || creating || saving} onClick={() => setManageOpen(true)}>管理标签</Button>
        </div>
      </div>
      <TagManagerModal
        open={manageOpen}
        onClose={() => setManageOpen(false)}
        tags={catalog}
        onChanged={onTagsChanged}
      />
      {operations.length ? (
        <details className="mt-4 border-t border-slate-100 pt-3">
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
