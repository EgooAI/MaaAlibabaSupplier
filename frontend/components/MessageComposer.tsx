"use client";

import { DownOutlined, ExpandAltOutlined, SendOutlined, ShrinkOutlined, ToolOutlined } from "@ant-design/icons";
import { Button, Dropdown, Input, Space, Tooltip } from "antd";
import type { MenuProps } from "antd";
import { useMemo, useRef, useState, type ComponentRef, type ReactNode } from "react";

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
  onFill?: () => void;
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
  onFill,
  compact = false,
  footer,
}: MessageComposerProps) {
  const [expanded, setExpanded] = useState(false);
  const textareaRef = useRef<ComponentRef<typeof Input.TextArea>>(null);
  const expandLabel = expanded ? "收起长文本编辑" : "展开编辑长文本";
  const menuItems: MenuProps["items"] = useMemo(
    () => tools.map((tool) => ({ key: tool.key, label: tool.label, disabled: tool.disabled })),
    [tools],
  );
  const canSend = !disabled && !sendDisabled && !loading && Boolean(value.trim());

  return (
    <div className="relative w-full">
      <div className={`flex w-full flex-col ${compact ? "overflow-hidden rounded-xl border border-slate-200 bg-white transition-colors [&:has(textarea:focus)]:border-blue-400" : ""}`}>
        <Input.TextArea
          ref={textareaRef}
          autoSize={compact && expanded ? false : { minRows: compact ? 1 : 3, maxRows: compact ? 6 : 8 }}
          variant={compact ? "borderless" : "outlined"}
          className={compact ? "focus:!outline-none" : "!rounded-b-none"}
          style={compact ? { paddingRight: 40, ...(expanded ? { height: "min(50dvh, 400px)", resize: "none" } : {}) } : undefined}
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
        <div className={compact ? "flex flex-wrap items-center justify-between gap-2 px-2 pb-2" : "flex justify-between rounded-b-lg border border-t-0 border-slate-200 bg-slate-50 p-3"}>
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            {footer}
            {tools.length ? (
              <Dropdown menu={{ items: menuItems, onClick: ({ key }) => onToolClick?.(key) }} trigger={["click"]}>
                <Button type={compact ? "text" : "default"} size={compact ? "small" : "middle"} icon={<ToolOutlined />}>{compact ? "更多" : "工具栏"}</Button>
              </Dropdown>
            ) : <span />}
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-3">
            {compact ? <span className="hidden text-[11px] text-slate-400 2xl:inline">Ctrl / ⌘ + Enter</span> : null}
            <Space.Compact>
            <Button
              type="primary"
              className={compact ? onFill ? "!rounded-s-full" : "!rounded-full" : ""}
              icon={<SendOutlined />}
              onClick={onSend}
              loading={loading}
              disabled={!canSend}
            >
              {sendLabel}
            </Button>
            {onFill ? <Dropdown trigger={["click"]} menu={{ items: [{ key: "fill", label: "仅填入客户端" }], onClick: onFill }} disabled={!canSend}>
              <Button type="primary" className={compact ? "!rounded-e-full" : undefined} aria-label="更多发送方式" icon={<DownOutlined />} disabled={!canSend} />
            </Dropdown> : null}
            </Space.Compact>
          </div>
        </div>
      </div>
      {compact ? (
        <Tooltip title={expandLabel}>
          <Button
            type="text"
            size="small"
            aria-label={expandLabel}
            aria-expanded={expanded}
            icon={expanded ? <ShrinkOutlined /> : <ExpandAltOutlined />}
            className="!absolute right-1.5 top-1.5 z-10 text-slate-400 hover:text-blue-500"
            disabled={disabled}
            onClick={() => {
              setExpanded((open) => !open);
              textareaRef.current?.focus({ preventScroll: true });
            }}
          />
        </Tooltip>
      ) : null}
    </div>
  );
}
