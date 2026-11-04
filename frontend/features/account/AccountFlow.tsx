"use client";

import { Alert, Button, Card, Space, Steps, Typography } from "antd";
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
