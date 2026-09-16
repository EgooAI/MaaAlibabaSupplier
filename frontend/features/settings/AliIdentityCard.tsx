"use client";

import { App, Button, Card, Input, Modal, Space, Spin, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { AppTable } from "@/components/AppTable";
import { formatDateTime } from "@/domain/time";
import { backend } from "@/services/client";
import type { AliAccount } from "@/types/status";

function formatSize(bytes: number): string {
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  if (bytes >= 1 << 10) return `${(bytes / (1 << 10)).toFixed(1)} KB`;
  return `${bytes} B`;
}

export function AliIdentityCard({ dataDirOk }: { dataDirOk: boolean }) {
  const { message } = App.useApp();
  const [accounts, setAccounts] = useState<AliAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [actingId, setActingId] = useState<string | null>(null);
  const [keyModalId, setKeyModalId] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [keyError, setKeyError] = useState<string | null>(null);
  const [clearId, setClearId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setScanning(true);
    try {
      const next = await backend.listAliIds();
      setAccounts(next.accounts);
    } catch {
      message.error("账号列表加载失败");
    } finally {
      setScanning(false);
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    if (!dataDirOk) return;
    async function loadInitialAccounts() {
      await reload();
    }

    void loadInitialAccounts();
  }, [dataDirOk, reload]);

  const handleActivate = async (aliId: string) => {
    setActingId(aliId);
    try {
      const next = await backend.saveAliId(aliId);
      setAccounts(next.accounts);
      message.success(`已切换活动账号 ${aliId}，即时生效`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "切换失败");
    } finally {
      setActingId(null);
    }
  };

  const handleSaveKey = async () => {
    if (!keyModalId) return;
    setActingId(keyModalId);
    setKeyError(null);
    try {
      const next = await backend.saveAliKey(keyModalId, keyInput);
      setAccounts(next.accounts);
      setKeyModalId(null);
      setKeyInput("");
      message.success("AES Key 已保存");
    } catch (err) {
      setKeyError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setActingId(null);
    }
  };

  const handleClearKey = async () => {
    if (!clearId) return;
    setActingId(clearId);
    try {
      const next = await backend.clearAliKey(clearId);
      setAccounts(next.accounts);
      message.success("AES Key 已清除");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "清除失败");
    } finally {
      setActingId(null);
      setClearId(null);
    }
  };

  if (!dataDirOk) {
    return (
      <Card title="阿里账号选择">
        <Typography.Text type="secondary">请先在上方配置有效的数据目录，再扫描账号。</Typography.Text>
      </Card>
    );
  }

  return (
    <Card
      title="阿里账号选择"
      extra={
        <Button loading={scanning} onClick={() => void reload()}>
          重新扫描数据目录
        </Button>
      }
    >
      {loading ? (
        <Spin />
      ) : (
        <Space orientation="vertical" size="middle" className="w-full">
          <Typography.Text type="secondary">
            从数据目录自动分析出全部账号。每个账号的 AES Key 不同：程序运行时自动捕获，未捕获到的可手动设置（保存时用该账号数据库试解密校验）。切换活动账号即时生效；旧账号的会话仍在库中，切回即现。
          </Typography.Text>
          <AppTable<AliAccount>
            rowKey="ali_id"
            pagination={false}
            dataSource={accounts}
            columns={[
              { title: "Ali ID", dataIndex: "ali_id", key: "ali_id", render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
              {
                title: "AES Key",
                key: "key",
                render: (_, record) =>
                  record.has_key ? (
                    <Space>
                      <Typography.Text code>{record.key_preview}</Typography.Text>
                      <Tag color={record.key_source === "manual" ? "blue" : "default"}>{record.key_source === "manual" ? "手动" : "自动"}</Tag>
                    </Space>
                  ) : (
                    <Tag color="warning">未设置</Tag>
                  ),
              },
              {
                title: "数据库",
                key: "db",
                render: (_, record) => (
                  <Typography.Text type="secondary">
                    {formatSize(record.db_size)} · {formatDateTime(record.last_modified)}
                  </Typography.Text>
                ),
              },
              {
                title: "操作",
                key: "actions",
                render: (_, record) => (
                  <Space wrap>
                    {record.is_active ? (
                      <Tag color="success">当前活动</Tag>
                    ) : (
                      <Button type="link" size="small" loading={actingId === record.ali_id} onClick={() => void handleActivate(record.ali_id)}>
                        设为活动
                      </Button>
                    )}
                    <Button
                      type="link"
                      size="small"
                      onClick={() => {
                        setKeyModalId(record.ali_id);
                        setKeyInput("");
                        setKeyError(null);
                      }}
                    >
                      手动设置 Key
                    </Button>
                    {record.has_key ? (
                      <Button type="link" size="small" danger onClick={() => setClearId(record.ali_id)}>
                        清除 Key
                      </Button>
                    ) : null}
                  </Space>
                ),
              },
            ]}
          />
          <Modal
            title={`手动设置 AES Key（${keyModalId ?? ""}）`}
            open={keyModalId !== null}
            confirmLoading={actingId !== null}
            onCancel={() => setKeyModalId(null)}
            onOk={() => void handleSaveKey()}
            okText="保存并校验"
          >
            <Space orientation="vertical" className="w-full">
              <Typography.Text type="secondary">粘贴十六进制 Key（16/24/32 字节），保存时用该账号数据库试解密校验，不匹配会被拒绝。</Typography.Text>
              <Input.TextArea value={keyInput} onChange={(event) => setKeyInput(event.target.value)} rows={3} placeholder="例如 a1b2c3…" />
              {keyError ? <Typography.Text type="danger">{keyError}</Typography.Text> : null}
            </Space>
          </Modal>
          <ActionConfirmModal
            open={clearId !== null}
            title="清除 AES Key"
            warning={`确认清除账号 ${clearId ?? ""} 的 Key？清除后该账号需重新捕获或手动设置才能同步。`}
            okText="清除"
            loading={actingId !== null}
            onCancel={() => setClearId(null)}
            onConfirm={() => void handleClearKey()}
          />
        </Space>
      )}
    </Card>
  );
}
