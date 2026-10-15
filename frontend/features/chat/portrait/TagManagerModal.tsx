"use client";

import { App, Button, Input, Modal, Tag, Typography } from "antd";
import { useState } from "react";
import { AccountChangedError, useAccountBackend } from "@/features/account/AccountProvider";
import type { PortraitTag } from "@/types/portrait";

/** 管理现有业务标签：改名、维护说明、停用与恢复。
 *
 * 目录数据由父级传入（共享字典），任何更新成功后调用 onChanged 触发父级重读；
 * 服务端仍是唯一权威，重复名/停用约束失败时展示服务端错误文案。
 */
export function TagManagerModal({ open, onClose, tags, onChanged }: {
  open: boolean;
  onClose: () => void;
  tags: PortraitTag[];
  onChanged?: () => void;
}) {
  const { message } = App.useApp();
  const backend = useAccountBackend();
  const [editingTagId, setEditingTagId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [saving, setSaving] = useState(false);
  // Reopening the modal starts from a clean draft; the adjustment happens at
  // render time instead of an effect (see "adjusting state when props change").
  const [syncedOpen, setSyncedOpen] = useState(open);
  if (syncedOpen !== open) {
    setSyncedOpen(open);
    if (!open) {
      setEditingTagId(null);
      setDraftName("");
      setDraftDescription("");
    }
  }

  function resetEdit() {
    setEditingTagId(null);
    setDraftName("");
    setDraftDescription("");
  }

  function beginEdit(tag: PortraitTag) {
    setEditingTagId(tag.tag_id);
    setDraftName(tag.name);
    setDraftDescription(tag.description);
  }

  async function updateTag(tagId: string, input: { name?: string; description?: string; archived?: boolean }) {
    if (saving) return;
    setSaving(true);
    try {
      await backend.updatePortraitTag(tagId, input);
      if (input.archived === true) message.success("标签已停用，历史关联保留");
      else if (input.archived === false) message.success("标签已恢复");
      else message.success("标签已更新");
      resetEdit();
      onChanged?.();
    } catch (error) {
      if (!(error instanceof AccountChangedError)) {
        message.error(error instanceof Error ? error.message : "标签更新失败");
      }
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit(tag: PortraitTag) {
    const name = draftName.trim();
    const description = draftDescription.trim();
    if (!name) {
      message.warning("标签名称不能为空");
      return;
    }
    const input: { name?: string; description?: string } = {};
    if (name !== tag.name) input.name = name;
    if (description !== tag.description) input.description = description;
    if (!Object.keys(input).length) {
      message.info("没有需要保存的修改");
      resetEdit();
      return;
    }
    await updateTag(tag.tag_id, input);
  }

  return (
    <Modal
      title="管理业务标签"
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={560}
      destroyOnHidden
    >
      <Typography.Paragraph type="secondary" className="!mb-2 !text-xs">
        改名对全部工作区生效；停用标签不能新增，但对客户的既有关联与筛选仍保留。
      </Typography.Paragraph>
      <div className="flex flex-col">
        {tags.length ? tags.map((tag) => (
          <div key={tag.tag_id} className="flex flex-col gap-2 border-b border-slate-100 px-1 py-2.5 last:border-b-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate text-[13px] font-medium">{tag.name}</span>
              {tag.archived ? <Tag color="default" className="!m-0 !text-[11px]">已停用</Tag> : null}
              <span className="text-[11px] text-slate-400">{tag.usage_count} 位客户关联</span>
              <span className="ml-auto flex items-center gap-1.5">
                {editingTagId === tag.tag_id ? null : (
                  <Button size="small" disabled={saving} onClick={() => beginEdit(tag)}>编辑</Button>
                )}
                <Button
                  size="small"
                  disabled={saving}
                  onClick={() => {
                    if (editingTagId === tag.tag_id) resetEdit();
                    void updateTag(tag.tag_id, { archived: !tag.archived });
                  }}
                >
                  {tag.archived ? "恢复" : "停用"}
                </Button>
              </span>
            </div>
            {editingTagId === tag.tag_id ? (
              <div className="flex flex-col gap-1.5 rounded-md border border-slate-200 bg-slate-50 p-2">
                <Input
                  aria-label={`标签名称：${tag.name}`}
                  size="small"
                  maxLength={40}
                  value={draftName}
                  onChange={(event) => setDraftName(event.target.value)}
                  onPressEnter={() => void saveEdit(tag)}
                />
                <Input
                  aria-label={`标签说明：${tag.name}`}
                  size="small"
                  maxLength={200}
                  placeholder="标签说明（可选，便于理解口径）"
                  value={draftDescription}
                  onChange={(event) => setDraftDescription(event.target.value)}
                  onPressEnter={() => void saveEdit(tag)}
                />
                <div className="mt-1 flex justify-end gap-2">
                  <Button size="small" disabled={saving} onClick={resetEdit}>取消</Button>
                  <Button size="small" type="primary" loading={saving} onClick={() => void saveEdit(tag)}>保存</Button>
                </div>
              </div>
            ) : tag.description ? (
              <Typography.Text type="secondary" className="!text-[11px]">{tag.description}</Typography.Text>
            ) : null}
          </div>
        )) : (
          <Typography.Text type="secondary" className="py-6 text-center !text-xs">暂无业务标签</Typography.Text>
        )}
      </div>
    </Modal>
  );
}
