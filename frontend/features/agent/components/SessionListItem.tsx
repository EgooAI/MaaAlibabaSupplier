"use client";

import { CopyOutlined, DeleteOutlined, MoreOutlined, ReloadOutlined } from "@ant-design/icons";
import { Avatar, Button, Dropdown, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { avatarColorOf, avatarInitialOf } from "@/domain/chat/avatarModel";
import { formatAgentSessionDate } from "@/domain/agent/agentModel";
import type { AgentTestSession } from "@/types/agent";

type SessionListItemProps = {
  session: AgentTestSession;
  agentName: string;
  active: boolean;
  disabled: boolean;
  deleting: boolean;
  onClick: () => void;
  onCopy: () => void;
  onDelete: () => void;
};

export function SessionListItem({ session, agentName, active, disabled, deleting, onClick, onCopy, onDelete }: SessionListItemProps) {
  const menuItems: MenuProps["items"] = [
    { key: "copy", icon: <CopyOutlined />, label: "复制", disabled: deleting },
    { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true, disabled: deleting },
  ];

  function handleMenuClick(info: Parameters<NonNullable<MenuProps["onClick"]>>[0]) {
    info.domEvent.stopPropagation();
    if (info.key === "copy") void onCopy();
    if (info.key === "delete") onDelete();
  }

  return (
    <div aria-current={active ? "true" : undefined} className={`rounded-lg border px-2 py-2 ${disabled ? "cursor-default" : "cursor-pointer"} ${active ? "border-blue-300 bg-blue-100 shadow-sm" : "border-transparent hover:border-slate-200 hover:bg-slate-50"}`} onClick={onClick}>
      <div className="flex items-center gap-2">
        <Avatar size={40} className="shrink-0" style={{ backgroundColor: avatarColorOf(session.agentId) }}>
          {avatarInitialOf(agentName)}
        </Avatar>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <Typography.Text strong ellipsis>{session.title}</Typography.Text>
            <Typography.Text className="shrink-0 text-xs">{formatAgentSessionDate(session.createdAt)}</Typography.Text>
          </div>
          <div className="mt-1">
            <Tag color="blue">{agentName}</Tag>
          </div>
        </div>
        <Dropdown menu={{ items: menuItems, onClick: handleMenuClick }} trigger={["click"]} disabled={disabled}>
          <Button
            type="text"
            size="small"
            icon={deleting ? <ReloadOutlined spin /> : <MoreOutlined />}
            aria-label={`操作会话：${session.title}`}
            onClick={(event) => event.stopPropagation()}
          />
        </Dropdown>
      </div>
    </div>
  );
}
