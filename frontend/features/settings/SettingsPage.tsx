"use client";

import { PoweroffOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Result, Space, Typography } from "antd";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { useShutdownApp } from "./hooks/useShutdownApp";

export function SettingsPage() {
  const { confirmOpen, setConfirmOpen, terminating, terminated, confirmShutdown } = useShutdownApp();

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
