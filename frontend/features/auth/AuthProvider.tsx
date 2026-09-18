"use client";

import { Alert, Button, Card, Input, Space, Spin, Typography } from "antd";
import { createContext, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { authSession } from "@/services/authSession";

const AuthContext = createContext<ReturnType<typeof authSession.get> | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const session = useSyncExternalStore(authSession.subscribe, authSession.get, authSession.server);
  useEffect(() => {
    const onStorage = (event: StorageEvent) => { void authSession.onStorage(event); };
    window.addEventListener("storage", onStorage);
    void authSession.bootstrap();
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  return <AuthContext.Provider value={session}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const session = useContext(AuthContext);
  if (!session) throw new Error("AuthProvider is required");
  return { ...session, logout: authSession.logout };
}

export function AuthGate({ children }: { children: ReactNode }) {
  const session = useAuth();
  if (session.phase === "authenticated") return <div key={session.generation}>{children}</div>;
  return <Login key={session.generation} />;
}

function Login() {
  const session = useAuth();
  const [secret, setSecret] = useState("");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (session.retryAt <= Date.now()) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= session.retryAt) clearInterval(timer);
    }, 250);
    return () => clearInterval(timer);
  }, [session.retryAt]);
  const remaining = Math.max(0, Math.ceil((session.retryAt - now) / 1000));
  const busy = session.phase === "loading" || session.phase === "verifying";
  return (
    <main className="flex min-h-dvh items-center justify-center bg-slate-50 p-4">
      <Card className="w-full max-w-md shadow-sm">
        <Space orientation="vertical" size="large" className="w-full">
          <div>
            <Typography.Title level={3}>阿里国际站运营助手</Typography.Title>
            <Typography.Text type="secondary">登录后继续访问当前页面</Typography.Text>
          </div>
          {session.warning ? <Alert type="warning" showIcon title={session.warning} /> : null}
          {session.error ? <Alert type="error" showIcon title={session.error} /> : null}
          {remaining > 0 ? <Alert type="info" title={`请等待 ${remaining} 秒后重试（登录服务全局限速）`} /> : null}
          {busy ? <div role="status"><Spin size="small" /> 正在验证登录状态...</div> : null}
          {session.phase === "signedOut" ? (
            <form onSubmit={(event) => {
              event.preventDefault();
              const submitted = secret;
              setSecret("");
              void authSession.login(submitted);
            }}>
              <Space orientation="vertical" size="middle" className="w-full">
                <label htmlFor="auth-secret">访问密钥</label>
                <Input.Password id="auth-secret" autoComplete="off" value={secret} onChange={(event) => setSecret(event.target.value)} disabled={remaining > 0} />
                <Button type="primary" htmlType="submit" block disabled={!secret || remaining > 0}>登录</Button>
              </Space>
            </form>
          ) : null}
          {session.phase === "retry" ? <Button type="primary" block disabled={remaining > 0} onClick={() => void authSession.retry()}>重新验证登录</Button> : null}
          {session.phase === "persist" ? <Space orientation="vertical" className="w-full">
            <Button type="primary" block onClick={authSession.persist}>重试保存凭证</Button>
            <Button block onClick={authSession.useTemporarySession}>仅在本页临时登录</Button>
          </Space> : null}
          {session.phase !== "signedOut" && session.phase !== "loading" ? <Button block onClick={() => void session.logout()}>取消并退出登录</Button> : null}
        </Space>
      </Card>
    </main>
  );
}
