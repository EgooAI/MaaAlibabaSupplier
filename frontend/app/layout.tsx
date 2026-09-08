import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { AntdProviders } from "@/components/AntdProviders";
import { AppShell } from "@/components/AppShell";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "外贸运营智能工作台",
  description: "基于 Ant Design 的外贸运营、聊天、卡片、系统状态与 Agent 控制台演示前端",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${geistSans.variable} ${geistMono.variable} h-full`}>
      <body className="min-h-full">
        <AntdRegistry>
          <AntdProviders>
            <AppShell>{children}</AppShell>
          </AntdProviders>
        </AntdRegistry>
      </body>
    </html>
  );
}
