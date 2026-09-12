"use client";

import { Avatar, Badge, Button, Checkbox, Listy, Radio, Space, Typography } from "antd";
import { useMemo } from "react";
import { StatusTag } from "@/components/StatusTag";
import { conversationAvatarUrl } from "@/domain/chat/avatarModel";
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
    <Space orientation="vertical" className="w-full" size="middle">
      <Radio.Group
        size="small"
        value={groupMode}
        onChange={(event) => onGroupModeChange?.(event.target.value)}
        options={[{ label: "按时间", value: "time" }, { label: "按状态", value: "status" }, { label: "按对话数", value: "count" }]}
        optionType="button"
      />
      {groups.map((group) => {
        const groupIds = group.items.map((item) => item.id);
        const groupSelected = groupIds.filter((id) => selectedIds.includes(id)).length;
        return (
          <div key={group.label}>
            <div className="flex items-center justify-between gap-2 px-1">
              <Typography.Text className="text-xs">{group.label} · 已选 {groupSelected} / {group.items.length}</Typography.Text>
              {selectable ? (
                <Space size={4}>
                  <Button type="link" size="small" onClick={() => onSelectGroup?.(groupIds, true)}>选中本组</Button>
                  <Button type="link" size="small" onClick={() => onSelectGroup?.(groupIds, false)}>取消本组</Button>
                </Space>
              ) : null}
            </div>
            <Listy
              items={group.items}
              rowKey="id"
              virtual={false}
              itemRender={(item: Conversation) => (
                <div
                  className={`cursor-pointer rounded-lg px-2 ${activeId === item.id ? "bg-blue-50" : "hover:bg-slate-50"}`}
                  onClick={() => onSelect?.(item.id)}
                >
                  <div className="flex w-full gap-2">
                    {selectable ? <Checkbox checked={selectedIds.includes(item.id)} onClick={(event) => event.stopPropagation()} onChange={() => onToggleSelected?.(item.id)} /> : null}
                    <Avatar src={conversationAvatarUrl(item.customer.id)} size={40} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <Typography.Text strong ellipsis>{item.customer.name}</Typography.Text>
                        <Badge count={item.unreadCount} size="small" />
                      </div>
                      <Typography.Text ellipsis className="block text-xs">{item.customer.company} · {item.customer.country}</Typography.Text>
                      {item.latestMessage ? <Typography.Text ellipsis className="block text-xs">{item.latestMessage}</Typography.Text> : null}
                      <div className="mt-2 flex items-center justify-between">
                        <StatusTag status={item.status} />
                        <Typography.Text className="text-xs">
                          {groupMode === "count" ? `对话 ${dialogueCountOf(item)} 条` : conversationTimeLabel(item.updatedAt)}
                        </Typography.Text>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            />
          </div>
        );
      })}
    </Space>
  );
}
