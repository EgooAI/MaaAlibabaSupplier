"use client";

import { Alert, App, Button, Card, Modal, Space, Steps, Typography } from "antd";
import { useState } from "react";
import { backend } from "@/services/client";
import { useAccount } from "./AccountProvider";
import { flowSteps } from "./flowSteps";
import { SyncStatus } from "./SyncStatus";

export function ConnectionFlowCard() {
  const { snapshot, error, refreshing, refresh } = useAccount();
  const items = snapshot ? flowSteps(snapshot) : [];
  const current = Math.max(0, items.findIndex((step) => step.status !== "finish"));
  return (
    <Card title="接入进度" extra={<Button loading={refreshing} onClick={() => void refresh()}>刷新状态</Button>}>
      <Space orientation="vertical" size="middle" className="w-full">
        {error ? <Alert type="error" showIcon title={error} description="请确认后端正在运行，然后刷新状态。刷新只观察状态，不会操作客户端或调用模型。" /> : null}
        {!snapshot ? (
          <Typography.Text type="secondary">正在读取接入状态，请稍候。</Typography.Text>
        ) : (
          <Steps size="small" current={current} items={items.map(({ key, title, description, status }) => ({ key, title, description, status }))} />
        )}
      </Space>
    </Card>
  );
}

export function SyncCard() {
  const { snapshot } = useAccount();
  // The retry endpoint captures/verifies the key first, then synchronizes.
  // The label states what will actually happen for the current key state.
  const buttonLabel = { unverified: "验证密钥并同步", invalid: "重新验证密钥并同步", unavailable: "重新验证密钥并同步", valid: "立即同步" }[snapshot?.source.key_validation ?? "unverified"];
  return (
    <Card title="密钥与聊天同步">
      <Space orientation="vertical" size="middle" className="w-full">
        <SyncStatus buttonLabel={buttonLabel} />
        {snapshot?.source.last_error ? (
          <Alert type="error" showIcon title={snapshot.source.last_error} description="请检查数据目录、账号及密钥，修正后点击上方按钮重新验证并同步。" />
        ) : null}
      </Space>
    </Card>
  );
}

export function ClientCard() {
  const { snapshot, blocked, mutating, mutate } = useAccount();
  const { message } = App.useApp();
  const [confirmation, setConfirmation] = useState<{ epoch: string; window: string; seller: string }>();
  const run = async (operation: () => Promise<unknown>) => {
    try { await mutate(operation); }
    catch (err) { message.error(err instanceof Error ? err.message : "操作失败，请刷新状态后重试"); }
  };
  if (!snapshot) return null;
  const current = confirmation && !blocked && snapshot.account.epoch === confirmation.epoch && snapshot.client.connected && snapshot.client.window_generation === confirmation.window;
  const canAct = !blocked && !mutating && Boolean(snapshot.account.self_ali_id);
  return (
    <Card title="客户端接入">
      <Space orientation="vertical" size="middle" className="w-full">
        {!snapshot.client.confirmed ? (
          <Alert type="warning" showIcon title="卖家身份尚未人工确认" description="确认前可以阅读聊天、编辑草稿；发送、填入测试与跳转联系人不可用。" />
        ) : null}
        <Space wrap>
          <Button loading={mutating} disabled={!canAct} onClick={() => void run(() => backend.connectClient(snapshot.account.epoch))}>{snapshot.client.connected ? "重新接入客户端" : "接入客户端"}</Button>
          <Button type="primary" disabled={!canAct || !snapshot.client.connected || snapshot.client.confirmed || !snapshot.client.window_generation} onClick={() => setConfirmation({ epoch: snapshot.account.epoch, window: snapshot.client.window_generation, seller: snapshot.account.self_ali_id })}>核对并确认卖家</Button>
        </Space>
        <Typography.Text type="secondary">接入仅初始化客户端，不执行点击；重新接入或窗口变化后需重新人工确认。</Typography.Text>
      </Space>
      <Modal title="人工确认客户端卖家身份" open={Boolean(confirmation)} onCancel={() => setConfirmation(undefined)} okText="我已核对，确认是此卖家" okButtonProps={{ disabled: !current }} confirmLoading={mutating} onOk={() => {
        if (!current || !confirmation) return;
        const selected = confirmation;
        setConfirmation(undefined);
        void run(() => backend.confirmClient(selected.epoch, selected.window));
      }}>
        <Typography.Paragraph>请在阿里客户端窗口查看当前登录账号，确认其卖家 Ali ID 与下面一致。不要仅凭昵称判断。</Typography.Paragraph>
        <Typography.Paragraph strong>{confirmation?.seller}</Typography.Paragraph>
        {!current ? <Alert type="warning" title="账号或客户端窗口已变化，请关闭后重新核对。" /> : null}
      </Modal>
    </Card>
  );
}
