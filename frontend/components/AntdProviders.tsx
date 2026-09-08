"use client";

import { App, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { ReactNode } from "react";

export function AntdProviders({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: "#1677ff",
          borderRadius: 10,
          colorBgLayout: "#f5f7fb",
          fontFamily: "var(--font-geist-sans), Arial, Helvetica, sans-serif",
        },
        components: {
          Layout: {
            headerBg: "#ffffff",
            siderBg: "#0f172a",
          },
          Menu: {
            darkItemBg: "#0f172a",
            darkSubMenuItemBg: "#0f172a",
            darkItemSelectedBg: "#1677ff",
          },
          Card: {
            borderRadiusLG: 14,
          },
        },
      }}
    >
      <App>{children}</App>
    </ConfigProvider>
  );
}
