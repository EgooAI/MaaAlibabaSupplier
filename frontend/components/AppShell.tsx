"use client";

import { AppstoreOutlined, CommentOutlined, DashboardOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PoweroffOutlined, RobotOutlined, SelectOutlined, SettingOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Button, Layout, Menu, Tooltip, Typography } from "antd";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ProfileDrawer } from "./ProfileDrawer";
import { PAGE_TITLES, resolveOpenKeys, resolveSelectedKey } from "./routes.config";
import { useSelfInfo } from "./useSelfInfo";
import { useAccount } from "@/features/account/AccountProvider";

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
      { key: "/settings", icon: <PoweroffOutlined />, label: <Link href="/settings">系统设置</Link> },
    ],
  },
];

const pageTitles = PAGE_TITLES;

function NavigationMenu({ selectedKey, routeOpenKeys }: { selectedKey: string; routeOpenKeys: string[] }) {
  const [openKeys, setOpenKeys] = useState(routeOpenKeys);

  // Intentional route -> menu sync: follow route group while preserving user toggles.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOpenKeys((current) => {
      // Keep user toggles, but ensure route parent is open.
      const next = new Set(current);
      for (const key of routeOpenKeys) next.add(key);
      // Drop stale route groups that are no longer relevant (keep max 1 route group + user extras is overkill; sync to route).
      // Simple policy: if route group changed, follow route; otherwise keep user state.
      const routeGroup = routeOpenKeys[0];
      const hasRouteGroup = routeGroup ? next.has(routeGroup) : true;
      if (!hasRouteGroup) return routeOpenKeys;
      // If current already contains route group, preserve user toggles.
      if (routeGroup && current.includes(routeGroup)) return current;
      return routeOpenKeys.length ? routeOpenKeys : current;
    });
  }, [routeOpenKeys]);

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
  const { snapshot, blocked, generation } = useAccount();

  const selectedKey = resolveSelectedKey(pathname);
  const openKeys = resolveOpenKeys(pathname);
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
          <NavigationMenu selectedKey={selectedKey} routeOpenKeys={openKeys} />
        </div>
      </Sider>
      <Layout className="min-h-0">
        <Header className="sticky top-0 z-10 flex h-16 items-center justify-between border-b border-slate-100 px-6 shadow-sm">
          <Typography.Title level={4} className="!mb-0 truncate">
            {pageTitles[selectedKey] ?? "阿里国际站运营助手"}
          </Typography.Title>
          {!blocked && snapshot?.capabilities.read_chat ? <AccountProfile key={`${snapshot.account.epoch}:${generation}`} /> : <Typography.Text type="secondary">{snapshot?.account.self_ali_id || "尚未选择账号"}</Typography.Text>}
        </Header>
        <Content className="min-h-0 overflow-y-auto p-6">
          <div className="mx-auto max-w-[1480px]">{children}</div>
        </Content>
      </Layout>
    </Layout>
  );
}

function AccountProfile() {
  const [profileOpen, setProfileOpen] = useState(false);
  const { selfInfo, loading: profileLoading, error: profileError, avatarSource, setAvatarSource, reload: loadSelfInfo } = useSelfInfo();
  return <>
    <Button type="text" className="flex items-center gap-2" aria-label="打开个人信息" onClick={() => setProfileOpen(true)}>
      <Avatar size="small" src={avatarSource || undefined} icon={<UserOutlined />} onError={() => { setAvatarSource(""); return true; }} />
      <span className="max-w-40 truncate">{selfInfo ? [selfInfo.first_name, selfInfo.last_name].filter(Boolean).join(" ") || selfInfo.login_id : "个人信息"}</span>
    </Button>
    <ProfileDrawer
        selfInfo={selfInfo}
        loading={profileLoading}
        error={profileError}
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        onRetry={() => void loadSelfInfo()}
      />
  </>;
}
