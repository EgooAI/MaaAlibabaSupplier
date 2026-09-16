"use client";

import { Avatar, Badge, Button, Checkbox, Empty, Radio, Space, Typography } from "antd";
import { useMemo } from "react";
import { avatarColorOf, avatarInitialOf } from "@/domain/chat/avatarModel";
import { conversationTimeLabel, dialogueCountOf, groupConversations, sortConversations } from "@/domain/chat/chatModel";
import type { ConversationGroupMode } from "@/domain/chat/chatModel";
import type { Conversation } from "@/types/chatCanonical";

export function ConversationList({
  conversations,
  activeId,
  selectedIds = [],
  groupMode = "time",
  onGroupModeChange,
  onSelect,
  onToggleSelected,
  onSelectGroup,
  selectable = false,
}: {
  conversations: Conversation[];
  activeId?: string;
  selectedIds?: string[];
  groupMode?: ConversationGroupMode;
  onGroupModeChange?: (mode: ConversationGroupMode) => void;
  onSelect?: (id: string) => void;
  onToggleSelected?: (id: string) => void;
  onSelectGroup?: (ids: string[], value: boolean) => void;
  selectable?: boolean;
}) {
  const groups = useMemo(() => groupConversations(sortConversations(conversations), groupMode), [conversations, groupMode]);

  return (
    <Space orientation="vertical" className="w-full" size="small">
      <Radio.Group
        size="small"
        value={groupMode}
        onChange={(event) => onGroupModeChange?.(event.target.value)}
        options={[{ label: "按时间", value: "time" }, { label: "按状态", value: "status" }, { label: "按对话数", value: "count" }]}
        optionType="button"
      />
      {groups.length ? groups.map((group) => {
        const groupIds = group.items.map((item) => item.id);
        const groupSelected = groupIds.filter((id) => selectedIds.includes(id)).length;
        return (
          <div key={group.label}>
            <div className="flex items-center justify-between gap-2 px-1">
              <Typography.Text className="text-xs">{selectable ? `${group.label} · 已选 ${groupSelected} / ${group.items.length}` : group.label}</Typography.Text>
              {selectable ? (
                <Space size={4}>
                  <Button type="link" size="small" onClick={() => onSelectGroup?.(groupIds, true)}>选中本组</Button>
                  <Button type="link" size="small" onClick={() => onSelectGroup?.(groupIds, false)}>取消本组</Button>
                </Space>
              ) : null}
            </div>
            <div className="flex flex-col gap-1">
              {group.items.map((item: Conversation) => {
                const isActive = activeId === item.id;
                return (
                <div
                  key={item.id}
                  aria-current={isActive ? "true" : undefined}
                  className={`cursor-pointer rounded-lg border px-1.5 py-1.5 ${isActive ? "border-blue-300 bg-blue-100 shadow-sm" : "border-transparent hover:border-slate-200 hover:bg-slate-50"}`}
                  onClick={() => {
                    if (selectable) onToggleSelected?.(item.id);
                    else onSelect?.(item.id);
                  }}
                >
                  <div className="flex w-full gap-2">
                    {selectable ? <Checkbox checked={selectedIds.includes(item.id)} onClick={(event) => event.stopPropagation()} onChange={() => onToggleSelected?.(item.id)} /> : null}
                    <Avatar size={40} className="shrink-0" style={{ backgroundColor: avatarColorOf(item.customer.id) }}>
                      {avatarInitialOf(item.customer.name)}
                    </Avatar>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <Typography.Text strong ellipsis>{item.customer.name}</Typography.Text>
                        <Badge count={item.unreadCount} size="small" />
                      </div>
                      {item.customer.country ? <Typography.Text ellipsis className="block text-xs">{item.customer.country}</Typography.Text> : null}
                      {item.latestMessage ? <Typography.Text ellipsis className="block text-xs">{item.latestMessage}</Typography.Text> : null}
                      <div className="mt-2 flex items-center justify-end">
                        <Typography.Text className="text-xs">
                          {groupMode === "count" ? `对话 ${dialogueCountOf(item)} 条` : conversationTimeLabel(item.updatedAt)}
                        </Typography.Text>
                      </div>
                    </div>
                  </div>
                </div>
                );
              })}
            </div>
          </div>
        );
      }) : <Empty description="暂无会话" />}
    </Space>
  );
}
