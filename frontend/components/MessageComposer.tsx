"use client";

import { SendOutlined, ToolOutlined } from "@ant-design/icons";
import { Button, Dropdown, Input, Space } from "antd";
import type { MenuProps } from "antd";
import { useMemo } from "react";

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
  sendDisabled?: boolean;
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
  sendDisabled = false,
  onSend,
}: MessageComposerProps) {
  const menuItems: MenuProps["items"] = useMemo(
    () => tools.map((tool) => ({ key: tool.key, label: tool.label, disabled: tool.disabled })),
    [tools],
  );
  const canSend = !disabled && !sendDisabled && !loading && Boolean(value.trim());

  return (
    <Space.Compact className="w-full" orientation="vertical">
      <Input.TextArea
        autoSize={{ minRows: 3, maxRows: 8 }}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && canSend) {
            event.preventDefault();
            onSend();
          }
        }}
        placeholder={placeholder}
        disabled={disabled}
      />
      <div className="flex justify-between rounded-b-lg border border-t-0 border-slate-200 bg-slate-50 p-3">
        {tools.length ? (
          <Dropdown menu={{ items: menuItems, onClick: ({ key }) => onToolClick?.(key) }} trigger={["click"]}>
            <Button icon={<ToolOutlined />}>工具栏</Button>
          </Dropdown>
        ) : <span />}
        <Button type="primary" icon={<SendOutlined />} onClick={onSend} loading={loading} disabled={!canSend}>{sendLabel}</Button>
      </div>
    </Space.Compact>
  );
}
