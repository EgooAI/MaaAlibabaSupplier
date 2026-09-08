"use client";

import { SendOutlined, ToolOutlined } from "@ant-design/icons";
import { Button, Dropdown, Input, Space } from "antd";
import type { MenuProps } from "antd";

export type MessageComposerTool = {
  key: string;
  label: string;
  disabled?: boolean;
};

type MessageComposerProps = {
  value: string;
  onChange: (value: string) => void;
  tools?: MessageComposerTool[];
  onToolClick?: (key: string) => void;
  placeholder?: string;
  sendLabel?: string;
  loading?: boolean;
  disabled?: boolean;
  onSend: () => void;
};

export function MessageComposer({
  value,
  onChange,
  tools = [],
  onToolClick,
  placeholder = "输入消息...",
  sendLabel = "发送",
  loading = false,
  disabled = false,
  onSend,
}: MessageComposerProps) {
  const menuItems: MenuProps["items"] = tools.map((tool) => ({ key: tool.key, label: tool.label, disabled: tool.disabled }));

  return (
    <Space.Compact className="w-full" orientation="vertical">
      <Input.TextArea rows={4} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} disabled={disabled} />
      <div className="flex justify-between rounded-b-lg border border-t-0 border-slate-200 bg-slate-50 p-3">
        <Dropdown menu={{ items: menuItems, onClick: ({ key }) => onToolClick?.(key) }} trigger={["click"]} disabled={!tools.length}>
          <Button icon={<ToolOutlined />}>工具栏</Button>
        </Dropdown>
        <Button type="primary" icon={<SendOutlined />} onClick={onSend} loading={loading} disabled={disabled || !value.trim()}>{sendLabel}</Button>
      </div>
    </Space.Compact>
  );
}
