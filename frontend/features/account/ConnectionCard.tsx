"use client";

import { Alert, App, Button, Card, Descriptions, Modal, Space, Tag, Typography } from "antd";
import Link from "next/link";
import { useState } from "react";
import { backend } from "@/services/client";
import { useAccount } from "./AccountProvider";

export function ConnectionCard() {
  const { snapshot, blocked, error, refreshing, mutating, refresh, mutate } = useAccount();
  const { message } = App.useApp();
  const [confirmation, setConfirmation] = useState<{ epoch: string; window: string; seller: string }>();
  const run = async (operation: () => Promise<unknown>) => {
    try { await mutate(operation); }
    catch (err) { message.error(err instanceof Error ? err.message : "操作失败，请刷新状态后重试"); }
  };
  const current = confirmation && !blocked && snapshot?.account.epoch === confirmation.epoch && snapshot.client.connected && snapshot.client.window_generation === confirmation.window;
  const canAct = !blocked && !mutating && Boolean(snapshot?.account.self_ali_id);

  return (
    <Card title="账号接入" extra={<Button loading={refreshing} disabled={mutating} onClick={() => void refresh()}>刷新状态</Button>}>
      <Space orientation="vertical" size="middle" className="w-full">
        {error ? <Alert type="error" showIcon message={error} description="请确认后端正在运行，然后刷新状态。刷新只观察状态，不会操作客户端或调用模型。" /> : null}
        {!snapshot ? <Typography.Text>正在读取接入状态，请稍候。</Typography.Text> : <>
          <Descriptions column={1} size="small" items={[
            { key: "seller", label: "当前卖家", children: snapshot.account.self_ali_id || "尚未选择，请在下方选择账号" },
            { key: "dir", label: "数据目录", children: snapshot.account.data_dir || "尚未配置，请在下方填写" },
            { key: "source", label: "聊天同步", children: `${({ idle: "尚未同步", syncing: "同步中", ready: "已就绪", error: "同步失败" })[snapshot.source.phase]} / Key ${snapshot.source.key_validation}` },
            { key: "last", label: "最近同步成功", children: snapshot.source.last_success === null ? "尚无记录" : new Date(snapshot.source.last_success * 1000).toLocaleString() },
            { key: "client", label: "客户端", children: snapshot.client.detail || (snapshot.client.connected ? "已接入" : "尚未接入") },
          ]} />
          <Space wrap>
            <Tag color={!blocked && snapshot.capabilities.read_chat ? "success" : "default"}>读取聊天：{!blocked && snapshot.capabilities.read_chat ? "可用" : "不可用"}</Tag>
            <Tag color={!blocked && snapshot.capabilities.use_ai ? "success" : "default"}>使用 AI：{!blocked && snapshot.capabilities.use_ai ? "可用" : "不可用"}</Tag>
            <Tag color={!blocked && snapshot.capabilities.operate_client ? "success" : "default"}>操作客户端：{!blocked && snapshot.capabilities.operate_client ? "可用" : "不可用"}</Tag>
          </Space>
          {!snapshot.client.confirmed ? <Alert type="warning" showIcon message="卖家身份尚未人工确认" description="可以在数据就绪后阅读聊天、编辑草稿。每次接入客户端后，请核对窗口中登录的卖家，再明确确认；确认前不可发送、填入测试或跳转联系人。" /> : null}
          {snapshot.source.last_error ? <Alert type="error" showIcon message={snapshot.source.last_error} description={`${snapshot.source.error_code || "同步失败"}：请检查下方目录、账号及 Key，修正后点击“验证 Key 并同步”。`} /> : null}
          {snapshot.steps.map((step) => <div key={step.id}><Tag color={step.state === "ready" ? "success" : step.state === "error" ? "error" : "warning"}>{step.state === "ready" ? "就绪" : step.state === "error" ? "错误" : "待完成"}</Tag>{step.detail || step.id}</div>)}
          <Space wrap>
            <Button loading={mutating} disabled={!canAct || snapshot.data_dir.state !== "ok" || snapshot.source.phase === "syncing"} onClick={() => void run(() => backend.retryConnection(snapshot.account.epoch))}>验证 Key 并同步</Button>
            <Button loading={mutating} disabled={!canAct} onClick={() => void run(() => backend.connectClient(snapshot.account.epoch))}>{snapshot.client.connected ? "重新接入客户端" : "接入客户端"}</Button>
            <Button type="primary" disabled={!canAct || !snapshot.client.connected || snapshot.client.confirmed || !snapshot.client.window_generation} onClick={() => setConfirmation({ epoch: snapshot.account.epoch, window: snapshot.client.window_generation, seller: snapshot.account.self_ali_id })}>核对并确认卖家</Button>
          </Space>
          <Typography.Text type="secondary">接入仅初始化客户端，不执行点击。重新接入或窗口变化后需重新人工确认。首次验证与同步需你主动发起；Key 验证通过后，工作台自动检查并同步新消息。数据迁移由后端自动备份后执行。</Typography.Text>
          <Typography.Text>AI 模型：{snapshot.model.configured ? "配置可用，尚未验证实际调用" : "尚未配置可用模型"}</Typography.Text>
          <Space wrap><Link href="/agent/llm">配置 LLM</Link><Link href="/agent/regular-agents">前往 Agent 测试</Link></Space>
        </>}
      </Space>
      <Modal title="人工确认客户端卖家身份" open={Boolean(confirmation)} onCancel={() => setConfirmation(undefined)} okText="我已核对，确认是此卖家" okButtonProps={{ disabled: !current }} confirmLoading={mutating} onOk={() => {
        if (!current || !confirmation) return;
        const selected = confirmation;
        setConfirmation(undefined);
        void run(() => backend.confirmClient(selected.epoch, selected.window));
      }}>
        <Typography.Paragraph>请在阿里客户端窗口查看当前登录账号，确认其卖家 Ali ID 与下面一致。不要仅凭昵称判断。</Typography.Paragraph>
        <Typography.Paragraph strong>{confirmation?.seller}</Typography.Paragraph>
        {!current ? <Alert type="warning" message="账号或客户端窗口已变化，请关闭后重新核对。" /> : null}
      </Modal>
    </Card>
  );
}
