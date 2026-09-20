"use client";

import { PoweroffOutlined } from "@ant-design/icons";
import { Alert, App, Button, Card, Col, Input, List, Result, Row, Space, Spin, Typography } from "antd";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { AliIdentityCard } from "./AliIdentityCard";
import { useDataDirSettings } from "./hooks/useDataDirSettings";
import { useShutdownApp } from "./hooks/useShutdownApp";
import { useAccount } from "@/features/account/AccountProvider";
import { ClientCard, ConnectionFlowCard, SyncCard } from "@/features/account/AccountFlow";
import { InboxSettingsCard } from "./InboxSettingsCard";
import { UpdateCard } from "./UpdateCard";

type DataDirControls = ReturnType<typeof useDataDirSettings>;

function DataDirCard({ controls }: { controls: DataDirControls }) {
  const { message } = App.useApp();
  const { candidates, path, setPath, loading, scanning, saving, canSave, error, scan, save } = controls;

  const handleSave = async () => {
    if (await save()) message.success("数据目录已保存，即时生效");
  };

  return (
    <Card title="数据目录">
      {loading ? (
        <Spin />
      ) : (
        <Space orientation="vertical" size="middle" className="w-full">
          <Typography.Text type="secondary">
            配置本机阿里客户端数据目录，可自动探测各盘符根目录或手动填写；保存后即时生效，无需重启。
          </Typography.Text>
          {error ? <Alert type="error" showIcon title={error} /> : null}
          <Space.Compact className="w-full">
            <Input value={path} onChange={(event) => setPath(event.target.value)} placeholder="例如 D:\AlibabaSupplierData" />
            <Button type="primary" loading={saving} disabled={!canSave} onClick={() => void handleSave()}>
              保存
            </Button>
          </Space.Compact>
          <Button loading={scanning} onClick={() => void scan()}>自动探测</Button>
          {candidates.length > 0 ? (
            <List
              size="small"
              bordered
              header={<Typography.Text type="secondary">探测到以下候选目录，点击填入：</Typography.Text>}
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

export function AccountSetup() {
  const { snapshot } = useAccount();
  const dataDirControls = useDataDirSettings();
  return (
    <Row gutter={[16, 16]} className="w-full">
      <Col span={24}><ConnectionFlowCard /></Col>
      <Col xs={24} xl={12}><DataDirCard controls={dataDirControls} /></Col>
      <Col xs={24} xl={12}><SyncCard /></Col>
      <Col span={24}>
        <AliIdentityCard key={JSON.stringify([snapshot?.data_dir.path, snapshot?.account.epoch])} dataDirOk={snapshot?.data_dir.state === "ok"} />
      </Col>
      <Col xs={24} xl={12}><ClientCard /></Col>
      <Col xs={24} xl={12}><InboxSettingsCard /></Col>
    </Row>
  );
}

export function SettingsPage() {
  const { confirmOpen, setConfirmOpen, terminating, terminated, shutdownError, confirmShutdown } = useShutdownApp();

  const handleConfirm = () => void confirmShutdown();

  if (terminated) {
    return (
      <Card>
        <Result
          status="info"
          title="关闭请求已接受"
          subTitle="后端已接受退出请求，尚未确认所有进程退出。请检查程序状态后手动关闭本页面。"
        />
      </Card>
    );
  }

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <AccountSetup />
      <UpdateCard />

      <Card title="程序控制">
        <Space orientation="vertical" size="middle" className="w-full">
          {shutdownError ? <Alert type="warning" showIcon title={shutdownError} /> : null}
          <Typography.Text type="secondary">
            终止程序将退出后端服务及附带进程（MaaPiCli、Yak MITM 代理），本页面也会随之失效。如需再次使用，请重新启动程序。
          </Typography.Text>
          <Alert type="warning" showIcon title="终止前请确认没有正在执行的任务（批量发送、Agent 测试等），避免数据丢失。" />
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
