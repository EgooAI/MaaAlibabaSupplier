"use client";

import { PoweroffOutlined } from "@ant-design/icons";
import { Alert, App, Button, Card, Input, List, Result, Space, Spin, Tag, Typography } from "antd";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { AliIdentityCard } from "./AliIdentityCard";
import { useDataDirSettings } from "./hooks/useDataDirSettings";
import { useShutdownApp } from "./hooks/useShutdownApp";
import type { DataDirStatus } from "@/types/status";

type DataDirControls = ReturnType<typeof useDataDirSettings>;

function DataDirCard({ controls }: { controls: DataDirControls }) {
  const { message } = App.useApp();
  const { status, candidates, path, setPath, loading, scanning, saving, error, scan, save } = controls;

  const handleSave = async () => {
    if (await save()) message.success("数据目录已保存，即时生效");
  };

  const stateTag = (state: string) => {
    if (state === "ok") return <Tag color="success">有效</Tag>;
    if (state === "invalid") return <Tag color="error">无效</Tag>;
    return <Tag color="warning">未配置</Tag>;
  };

  return (
    <Card title="阿里客户端数据目录">
      {loading || !status ? (
        <Spin />
      ) : (
        <Space orientation="vertical" size="middle" className="w-full">
          <Typography.Text type="secondary">
            各用户安装位置不同（如 D:\AlibabaSupplierData），请配置本机阿里客户端数据目录。程序会在各盘符根目录自动探测，也可手动填写。保存后即时生效，无需重启。
          </Typography.Text>
          <Space>
            <Typography.Text>当前状态：</Typography.Text>
            {stateTag(status.state)}
            {status.path ? <Typography.Text code>{status.path}</Typography.Text> : null}
          </Space>
          {status.detail ? <Alert type={status.state === "ok" ? "success" : "warning"} showIcon message={status.detail} /> : null}
          {error ? <Alert type="error" showIcon message={error} /> : null}
          <Space.Compact className="w-full">
            <Input value={path} onChange={(event) => setPath(event.target.value)} placeholder="例如 D:\AlibabaSupplierData" />
            <Button type="primary" loading={saving} onClick={() => void handleSave()}>
              保存
            </Button>
          </Space.Compact>
          <Space>
            <Button loading={scanning} onClick={() => void scan()}>
              自动探测
            </Button>
            {candidates.length > 0 ? <Typography.Text type="secondary">点击候选可直接填入：</Typography.Text> : null}
          </Space>
          {candidates.length > 0 ? (
            <List
              size="small"
              bordered
              dataSource={candidates}
              renderItem={(item) => (
                <List.Item actions={[<Button key="use" type="link" size="small" onClick={() => setPath(item)}>填入</Button>]}>
                  <Typography.Text code>{item}</Typography.Text>
                </List.Item>
              )}
            />
          ) : null}
        </Space>
      )}
    </Card>
  );
}

export function SettingsPage() {
  const { confirmOpen, setConfirmOpen, terminating, terminated, confirmShutdown } = useShutdownApp();
  const dataDirControls = useDataDirSettings();
  const dataDirStatus: DataDirStatus | null = dataDirControls.status;

  const handleConfirm = () => void confirmShutdown();

  if (terminated) {
    return (
      <Card>
        <Result
          status="info"
          title="程序已终止"
          subTitle="后端及附带进程（MaaPiCli、Yak 代理）正在退出，本页面已不可用。浏览器标签页将尝试自动关闭，若未关闭请手动关闭。"
        />
      </Card>
    );
  }

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <DataDirCard controls={dataDirControls} />
      <AliIdentityCard dataDirOk={dataDirStatus?.state === "ok"} />

      <Card title="程序控制">
        <Space orientation="vertical" size="middle" className="w-full">
          <Typography.Text type="secondary">
            终止程序将退出后端服务及附带进程（MaaPiCli、Yak MITM 代理），本页面也会随之失效。如需再次使用，请重新启动程序。
          </Typography.Text>
          <Alert type="warning" showIcon message="终止前请确认没有正在执行的任务（批量发送、Agent 测试等），避免数据丢失。" />
          <Button danger icon={<PoweroffOutlined />} onClick={() => setConfirmOpen(true)}>
            终止程序
          </Button>
        </Space>
      </Card>

      <ActionConfirmModal
        open={confirmOpen}
        title="终止程序"
        warning="确认终止整个程序？后端服务与附带进程将全部退出，未保存的数据会丢失。"
        okText="终止程序"
        loading={terminating}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void handleConfirm()}
      />
    </Space>
  );
}
