export type RouteInfo = {
  path: string;
  title: string;
  navParent?: string;
};

/** 单一事实源：文件路由 == 导航 == 标题。新增页面只改这里。 */
export const ROUTES: RouteInfo[] = [
  { path: "/", title: "首页" },
  { path: "/chat", title: "聊天工作台", navParent: "/chat" },
  { path: "/chat/customer-sessions", title: "聊天工作台", navParent: "/chat" },
  { path: "/chat/agent-sessions", title: "Agent 会话", navParent: "/chat" },
  { path: "/batch", title: "批量管理" },
  { path: "/agent", title: "自动化", navParent: "/agent" },
  { path: "/agent/llm", title: "LLM", navParent: "/agent" },
  { path: "/agent/system-prompt", title: "Level SYSTEM_PROMPT", navParent: "/agent" },
  { path: "/agent/system-agents", title: "系统 Agent", navParent: "/agent" },
  { path: "/agent/regular-agents", title: "普通 Agent", navParent: "/agent" },
  { path: "/status", title: "系统状态", navParent: "/settings" },
  { path: "/settings", title: "系统设置", navParent: "/settings" },
];

export const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  ROUTES.map((route) => [route.path, route.title]),
);

export function resolveSelectedKey(pathname: string): string {
  if (pathname.startsWith("/chat/agent-sessions")) return "/chat/agent-sessions";
  if (pathname.startsWith("/agent/system-agents")) return "/agent/system-agents";
  if (pathname.startsWith("/agent/system-prompt")) return "/agent/system-prompt";
  if (pathname.startsWith("/agent/regular-agents")) return "/agent/regular-agents";
  if (pathname.startsWith("/agent/llm")) return "/agent/llm";
  if (pathname === "/agent" || pathname.startsWith("/agent/")) return "/agent/llm";
  if (pathname.startsWith("/settings")) return "/settings";
  if (pathname === "/chat" || pathname.startsWith("/chat/customer-sessions")) return "/chat/customer-sessions";
  if (pathname.startsWith("/status")) return "/status";
  if (pathname === "/") return "/";
  const top = `/${pathname.split("/")[1]}`;
  return PAGE_TITLES[top] ? top : "/";
}

export function resolveOpenKeys(pathname: string): string[] {
  if (pathname === "/chat" || pathname.startsWith("/chat/")) return ["/chat"];
  if (pathname.startsWith("/agent/") || pathname === "/agent") return ["/agent"];
  if (pathname.startsWith("/status") || pathname.startsWith("/settings")) return ["/settings"];
  return [];
}
