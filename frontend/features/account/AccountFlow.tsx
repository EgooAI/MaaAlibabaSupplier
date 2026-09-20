"use client";

import { Alert, App, Button, Card, Space, Steps, Typography } from "antd";
import { backend } from "@/services/client";
import { useAccount } from "./AccountProvider";
import { flowSteps } from "./flowSteps";
import { SyncStatus } from "./SyncStatus";

export function ConnectionFlowCard() {
  const { snapshot, error, refreshing, refresh } = useAccount();
  const items = snapshot ? flowSteps(snapshot) : [];
  const firstUnfinished = items.findIndex((step) => step.status !== "finish");
  const current = firstUnfinished === -1 ? items.length : firstUnfinished;
  return (
    <Card title="接入进度" extra={<Button loading={refreshing} onClick={() => void refresh()}>刷新状态</Button>}>
      <Space orientation="vertical" size="middle" className="w-full">
        {error ? <Alert type="error" showIcon title={error} description="请确认后端正在运行，然后刷新状态。刷新只观察状态，不会操作客户端或调用模型。" /> : null}
        {!snapshot ? (
          <Typography.Text type="secondary">正在读取接入状态，请稍候。</Typography.Text>
        ) : (
          <Steps size="small" current={current} items={items.map(({ key, title, content, status }) => ({ key, title, content, status }))} />
        )}
      </Space>
    </Card>
  );
}

export function SyncCard() {
  const { snapshot } = useAccount();
  // The retry endpoint captures/verifies the key first, then synchronizes.
  // The label states what will actually happen for the current key state.
  const source = snapshot?.source;
  const retrying = Boolean(source?.last_error && source.retry_at != null && (source.key_validation === "unverified" || source.key_validation === "valid"));
  const buttonLabel = { unverified: "验证密钥并同步", verifying: "密钥验证中", invalid: "重新验证密钥并同步", unavailable: "重新验证密钥并同步", valid: "立即同步" }[source?.key_validation ?? "unverified"];
  return (
    <Card title="密钥与聊天同步">
      <Space orientation="vertical" size="middle" className="w-full">
        <SyncStatus buttonLabel={buttonLabel} />
        {source?.key_validation === "verifying" ? <Typography.Text type="secondary">正在自动验证已保存的密钥，验证成功后会继续同步聊天，无需重复点击。</Typography.Text> : null}
        {source?.last_error ? (
          <Alert type={retrying ? "warning" : "error"} showIcon title={source.last_error} description={retrying ? "后台将自动重试，无需重复点击；已有存档可能过期。" : "请检查数据目录、账号及密钥，修正后点击上方按钮重新验证并同步。"} />
        ) : null}
      </Space>
    </Card>
  );
}

export function ClientCard() {
  const { snapshot, blocked, mutating, mutate } = useAccount();
  const { message } = App.useApp();
  const run = async (operation: () => Promise<unknown>) => {
    try { await mutate(operation); }
    catch (err) { message.error(err instanceof Error ? err.message : "操作失败，请刷新状态后重试"); }
  };
  if (!snapshot) return null;
  const canAct = !blocked && !mutating && Boolean(snapshot.account.self_ali_id);
  return (
    <Card title="客户端接入">
      <Space orientation="vertical" size="middle" className="w-full">
        <Alert type={snapshot.client.connected ? "success" : "warning"} showIcon title={snapshot.client.connected ? "客户端已接入" : "客户端尚未接入"} description={snapshot.client.detail} />
        <Space wrap>
          <Button loading={mutating} disabled={!canAct} onClick={() => void run(() => backend.connectClient(snapshot.account.epoch))}>{snapshot.client.connected ? "重新接入客户端" : "接入客户端"}</Button>
        </Space>
        <Typography.Text type="secondary">选择卖家并接入客户端后即可操作。接入仅初始化客户端，不执行点击；发送和填入仍需查看截图并确认联系人与内容。</Typography.Text>
      </Space>
    </Card>
  );
}
