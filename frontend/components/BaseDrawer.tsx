"use client";

import { Drawer } from "antd";
import type { ReactNode } from "react";

export function BaseDrawer({ title, open, onClose, children, size = 520 }: { title: ReactNode; open: boolean; onClose: () => void; children: ReactNode; size?: number }) {
  return (
    <Drawer title={title} size={size} open={open} onClose={onClose} destroyOnHidden>
      {children}
    </Drawer>
  );
}
