"use client";

import { Button, Drawer } from "antd";
import type { ReactNode } from "react";

export type MessageAction = {
  key: string;
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  onSelect: () => void;
};

/** Bottom sheet listing the actions of one message, opened by a long press on touch devices. */
export function MessageActionSheet({ open, detail, actions, onClose, onClosed }: {
  open: boolean;
  /** Exact timestamp shown above the actions; touch devices have no hover tooltip. */
  detail?: string;
  actions: MessageAction[];
  onClose: () => void;
  onClosed: () => void;
}) {
  return (
    <Drawer
      placement="bottom"
      size="auto"
      open={open}
      closable={false}
      onClose={onClose}
      afterOpenChange={(visible) => { if (!visible) onClosed(); }}
      styles={{ body: { padding: 8, paddingBottom: "calc(8px + env(safe-area-inset-bottom))" } }}
    >
      {detail ? <div className="px-3 pb-1 pt-2 text-center text-xs text-slate-400">{detail}</div> : null}
      {actions.map((action) => (
        <Button
          key={action.key}
          block
          size="large"
          type="text"
          icon={action.icon}
          disabled={action.disabled}
          className="!justify-start"
          onClick={() => { onClose(); action.onSelect(); }}
        >
          {action.label}
        </Button>
      ))}
    </Drawer>
  );
}
