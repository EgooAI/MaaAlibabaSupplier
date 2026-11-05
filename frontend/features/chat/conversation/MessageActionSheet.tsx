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
export function MessageActionSheet({ open, actions, onClose, onClosed }: {
  open: boolean;
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
