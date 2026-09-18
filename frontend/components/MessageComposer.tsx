"use client";

import { ExpandAltOutlined, SendOutlined, ToolOutlined } from "@ant-design/icons";
import { Button, Dropdown, Input, Modal, Space, Tooltip } from "antd";
import type { MenuProps } from "antd";
import { useMemo, useState, type ReactNode } from "react";

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
  compact?: boolean;
  footer?: ReactNode;
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
  compact = false,
  footer,
}: MessageComposerProps) {
  const [expanded, setExpanded] = useState(false);
  const [expandedDraft, setExpandedDraft] = useState("");
  const menuItems: MenuProps["items"] = useMemo(
    () => tools.map((tool) => ({ key: tool.key, label: tool.label, disabled: tool.disabled })),
    [tools],
  );
  const canSend = !disabled && !sendDisabled && !loading && Boolean(value.trim());

  return (
    <div className="relative w-full">
      <Space.Compact className={`w-full ${compact ? "overflow-hidden rounded-xl border border-slate-200 bg-white transition-colors [&:has(textarea:focus)]:border-blue-400" : ""}`} orientation="vertical">
        <Input.TextArea
          autoSize={{ minRows: compact ? 1 : 3, maxRows: compact ? 6 : 8 }}
          variant={compact ? "borderless" : "outlined"}
          className={compact ? "focus:!outline-none" : undefined}
          style={compact ? { paddingRight: 40 } : undefined}
          aria-label="消息内容"
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
        <div className={compact ? "flex items-center justify-between gap-2 px-2 pb-2" : "flex justify-between rounded-b-lg border border-t-0 border-slate-200 bg-slate-50 p-3"}>
          <div className="flex items-center gap-1">
            {footer}
            {tools.length ? (
              <Dropdown menu={{ items: menuItems, onClick: ({ key }) => onToolClick?.(key) }} trigger={["click"]}>
                <Button type={compact ? "text" : "default"} size={compact ? "small" : "middle"} icon={<ToolOutlined />}>{compact ? "更多" : "工具栏"}</Button>
              </Dropdown>
            ) : <span />}
          </div>
          <div className="flex items-center gap-3">
            {compact ? <span className="hidden text-[11px] text-slate-400 2xl:inline">Ctrl / ⌘ + Enter</span> : null}
            <Button
              type="primary"
              className={compact ? "!rounded-full" : ""}
              icon={<SendOutlined />}
              onClick={onSend}
              loading={loading}
              disabled={!canSend}
            >
              {sendLabel}
            </Button>
          </div>
        </div>
      </Space.Compact>
      {compact ? (
        <Tooltip title="展开编辑长文本">
          <Button
            type="text"
            size="small"
            aria-label="展开编辑长文本"
            icon={<ExpandAltOutlined />}
            className="!absolute right-1.5 top-1.5 z-10 text-slate-400 hover:text-blue-500"
            disabled={disabled}
            onClick={() => {
              setExpandedDraft(value);
              setExpanded(true);
            }}
          />
        </Tooltip>
      ) : null}
      <Modal
        title="编辑消息内容"
        open={expanded}
        okText="完成"
        cancelText="取消"
        onOk={() => {
          onChange(expandedDraft);
          setExpanded(false);
        }}
        onCancel={() => setExpanded(false)}
        destroyOnHidden
        width={640}
      >
        <Input.TextArea
          aria-label="消息内容（展开编辑）"
          value={expandedDraft}
          onChange={(event) => setExpandedDraft(event.target.value)}
          autoSize={{ minRows: 12, maxRows: 18 }}
          placeholder={placeholder}
          disabled={disabled}
          showCount
        />
      </Modal>
    </div>
  );
}
