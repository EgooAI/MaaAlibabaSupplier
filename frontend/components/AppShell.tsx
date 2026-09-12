"use client";

import { AppstoreOutlined, CommentOutlined, DashboardOutlined, FileTextOutlined, MenuFoldOutlined, MenuUnfoldOutlined, RobotOutlined, SelectOutlined, SettingOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Button, Layout, Menu, Tooltip, Typography } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { fallbackAvatarUrl } from "@/domain/chat/avatarModel";
import { backend } from "@/services/client";
import { ProfileDrawer } from "./ProfileDrawer";
import type { SelfInfo } from "@/types/home";

const { Header, Sider, Content } = Layout;

const navItems = [
  { key: "/", icon: <DashboardOutlined />, label: <Link href="/">首页</Link> },
  {
    key: "/chat",
    icon: <CommentOutlined />,
    label: "聊天工作台",
    children: [
      { key: "/chat/customer-sessions", icon: <CommentOutlined />, label: <Link href="/chat/customer-sessions">客户会话</Link> },
      { key: "/chat/agent-sessions", icon: <RobotOutlined />, label: <Link href="/chat/agent-sessions">Agent 会话</Link> },
    ],
  },
  { key: "/batch", icon: <SelectOutlined />, label: <Link href="/batch">批量管理</Link> },
  {
    key: "/agent",
    icon: <RobotOutlined />,
    label: "自动化",
    children: [
      { key: "/agent/llm", icon: <SettingOutlined />, label: <Link href="/agent/llm">LLM</Link> },
      { key: "/agent/system-prompt", icon: <FileTextOutlined />, label: <Link href="/agent/system-prompt">Level SYSTEM_PROMPT</Link> },
      { key: "/agent/system-agents", icon: <RobotOutlined />, label: <Link href="/agent/system-agents">系统 Agent</Link> },
      { key: "/agent/regular-agents", icon: <RobotOutlined />, label: <Link href="/agent/regular-agents">普通 Agent</Link> },
    ],
  },
  {
    key: "/settings",
    icon: <SettingOutlined />,
    label: "设置",
    children: [
      { key: "/status", icon: <AppstoreOutlined />, label: <Link href="/status">系统状态</Link> },
    ],
  },
];

const pageTitles: Record<string, string> = {
  "/": "首页",
  "/chat/customer-sessions": "聊天工作台",
  "/chat/agent-sessions": "Agent 会话",
  "/batch": "批量管理",
  "/agent/llm": "LLM",
  "/agent/system-prompt": "Level SYSTEM_PROMPT",
  "/agent/system-agents": "系统 Agent",
  "/agent/regular-agents": "普通 Agent",
  "/status": "系统状态",
};

function NavigationMenu({ selectedKey, routeOpenKeys }: { selectedKey: string; routeOpenKeys: string[] }) {  const [openKeys, setOpenKeys] = useState(routeOpenKeys);

  return (
    <Menu
      theme="dark"
      mode="inline"
      selectedKeys={[selectedKey]}
      openKeys={openKeys}
      onOpenChange={setOpenKeys}
      items={navItems}
      className="flex-1 border-0"
    />
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [selfInfo, setSelfInfo] = useState<SelfInfo | null>();
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState<string>();
  const [profileOpen, setProfileOpen] = useState(false);
  const [avatarSource, setAvatarSource] = useState(fallbackAvatarUrl);

  const loadSelfInfo = useCallback(async () => {
    setProfileLoading(true);
    setProfileError(undefined);
    try {
      const info = await backend.getSelfInfo();
      setSelfInfo(info);
      setAvatarSource(info?.avatar_url || fallbackAvatarUrl);
    } catch (error: unknown) {
      setSelfInfo(null);
      setAvatarSource(fallbackAvatarUrl);
      setProfileError(error instanceof Error ? error.message : "个人信息加载失败");
    } finally {
      setProfileLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => void loadSelfInfo());
  }, [loadSelfInfo]);

  const selectedKey = pathname.startsWith("/chat/agent-sessions")
    ? "/chat/agent-sessions"
    : pathname.startsWith("/agent/system-agents")
    ? "/agent/system-agents"
    : pathname.startsWith("/agent/system-prompt")
    ? "/agent/system-prompt"
    : pathname.startsWith("/agent/regular-agents")
      ? "/agent/regular-agents"
      : pathname.startsWith("/agent/llm")
        ? "/agent/llm"
        : pathname === "/chat" || pathname.startsWith("/chat/customer-sessions")
        ? "/chat/customer-sessions"
        : pathname.startsWith("/status")
          ? "/status"
          : pathname === "/"
            ? "/"
            : `/${pathname.split("/")[1]}`;
  const openKeys = pathname === "/chat" || pathname.startsWith("/chat/")
    ? ["/chat"]
    : pathname.startsWith("/agent/")
      ? ["/agent"]
      : pathname.startsWith("/status")
        ? ["/settings"]
        : [];
  return (
    <Layout className="fixed inset-0 min-h-0 items-stretch overflow-hidden">
      <Sider
        width={232}
        breakpoint="lg"
        collapsed={collapsed}
        collapsedWidth={72}
        trigger={null}
        onCollapse={setCollapsed}
        onBreakpoint={setCollapsed}
        className="h-full overflow-y-auto shadow-xl"
      >
        <div className="flex h-full min-h-0 flex-col">
          <div className={`flex h-16 items-center text-white ${collapsed ? "justify-center" : "gap-3 px-5"}`}>
            {collapsed ? (
              <Tooltip title="展开侧边栏" placement="right">
                <Button
                  type="text"
                  aria-label="展开侧边栏"
                  className="!text-white"
                  icon={<MenuUnfoldOutlined />}
                  onClick={() => setCollapsed(false)}
                />
              </Tooltip>
            ) : (
              <>
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-500 font-bold">AI</div>
                <Typography.Text className="!text-white" strong>外贸运营</Typography.Text>
                <Tooltip title="折叠侧边栏" placement="right">
                  <Button
                    type="text"
                    aria-label="折叠侧边栏"
                    className="!ml-auto !text-white"
                    icon={<MenuFoldOutlined />}
                    onClick={() => setCollapsed(true)}
                  />
                </Tooltip>
              </>
            )}
          </div>
          <NavigationMenu key={openKeys.join("|") || "root"} selectedKey={selectedKey} routeOpenKeys={openKeys} />
        </div>
      </Sider>
      <Layout className="min-h-0">
        <Header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-slate-100 px-6 shadow-sm">
          <Typography.Title level={4} className="!mb-0 truncate">
            {pageTitles[selectedKey] ?? "阿里国际站运营助手"}
          </Typography.Title>
          <Button type="text" className="flex items-center gap-2" aria-label="打开个人信息" onClick={() => setProfileOpen(true)}>
            <Avatar
              size="small"
              src={avatarSource}
              icon={<UserOutlined />}
              onError={() => {
                setAvatarSource(fallbackAvatarUrl);
                return true;
              }}
            />
            <span className="max-w-40 truncate">{selfInfo ? [selfInfo.first_name, selfInfo.last_name].filter(Boolean).join(" ") || selfInfo.login_id : "个人信息"}</span>
          </Button>
        </Header>
        <Content className="min-h-0 overflow-y-auto p-6">
          <div className="mx-auto max-w-[1480px]">{children}</div>
        </Content>
      </Layout>
      <ProfileDrawer
        selfInfo={selfInfo}
        loading={profileLoading}
        error={profileError}
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        onRetry={() => void loadSelfInfo()}
      />
    </Layout>
  );
}
