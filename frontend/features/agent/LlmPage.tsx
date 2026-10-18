"use client";

import { EditOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, InputNumber, Modal, Radio, Space, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { copyLlmLevelConfig, isValidToolRoundLimit } from "@/domain/agent/agentModel";
import type { LlmLevelConfig } from "@/types/agent";
import { AppTable } from "@/components/AppTable";
import { EMPTY_TEXT } from "@/components/empty";
import { useAgentWorkbench } from "./hooks/useAgentWorkbench";
import { PromptEditorField } from "./components/PromptEditorField";

type LlmLevelFormValues = Omit<LlmLevelConfig, "level">;

export function LlmPage() {
  const workbench = useAgentWorkbench();
  const [editingLevel, setEditingLevel] = useState<LlmLevelConfig>();
  const [saving, setSaving] = useState(false);
  const [copySource, setCopySource] = useState<LlmLevelConfig>();
  const [copyTarget, setCopyTarget] = useState<number>();
  const [copying, setCopying] = useState(false);
  const [form] = Form.useForm<LlmLevelFormValues>();

  const levels = useMemo(() => workbench.state?.llmLevels ?? [], [workbench.state?.llmLevels]);
  const unconfigured = levels.filter((item) => !item.model_name).length;
  const copyTargets = levels.filter((item) => item.level !== copySource?.level);

  function closeEditor() {
    setEditingLevel(undefined);
  }

  function openCopy(source: LlmLevelConfig) {
    setCopyTarget(undefined);
    setCopySource(source);
  }

  function closeCopy() {
    setCopyTarget(undefined);
    setCopySource(undefined);
  }

  async function confirmCopy() {
    if (!copySource || copyTarget === undefined) return;
    setCopying(true);
    try {
      const ok = await workbench.saveLlmLevel(
        copyLlmLevelConfig(copySource, copyTarget),
        `已复制到 Level ${copyTarget}`,
      );
      if (ok) closeCopy();
    } finally {
      setCopying(false);
    }
  }

  async function save(values: LlmLevelFormValues) {
    if (!editingLevel) return;
    setSaving(true);
    try {
      const ok = await workbench.saveLlmLevel({ ...values, level: editingLevel.level });
      if (ok) closeEditor();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Space orientation="vertical" size="large" className="w-full">
      <Card
        title={
          <span>
            LLM Level 配置 <Tag color="blue">{levels.length} 个层级</Tag>
            {unconfigured > 0 && <Tag color="warning">未配置 {unconfigured}</Tag>}
          </span>
        }
        loading={workbench.loading}
      >
        <AppTable
          rowKey="level"
          dataSource={levels}
          scroll={{ x: 1340 }}
          columns={[
            {
              title: "Level",
              dataIndex: "level",
              width: 100,
              render: (value: number) => <Tag color={value >= 3 ? "blue" : "default"}>L{value}</Tag>,
            },
            { title: "模型", dataIndex: "model_name", width: 240, ellipsis: true, render: (value: string) => value || <Tag>未配置</Tag> },
            { title: "服务地址", dataIndex: "base_url", width: 280, ellipsis: true },
            { title: "系统提示词", dataIndex: "system_prompt", width: 360, ellipsis: true },
            {
              title: "上下文",
              dataIndex: "context",
              width: 150,
              align: "right" as const,
              render: (value: number) => `${value.toLocaleString()} tokens`,
            },
            { title: "最大工具轮数", dataIndex: "max_tool_rounds", width: 150, align: "right" as const, render: (value: number | null) => value ?? "不限" },
            {
              title: "操作",
              width: 180,
              fixed: "right" as const,
              render: (_: unknown, record: LlmLevelConfig) => (
                <Space size={4}>
                  <Button size="small" icon={<EditOutlined />} onClick={() => setEditingLevel(record)}>
                    {record.model_name ? "编辑" : "配置"}
                  </Button>
                  {/* 空白等级没有可复制的配置，只能作为复制目标。 */}
                  {record.model_name ? <Button size="small" type="link" onClick={() => openCopy(record)}>复制为</Button> : null}
                </Space>
              ),
            },
          ]}
          locale={{ emptyText: EMPTY_TEXT.llm }}
        />
      </Card>

      <Modal
        title={`${editingLevel?.model_name ? "编辑" : "配置"} Level ${editingLevel?.level ?? ""}`}
        open={Boolean(editingLevel)}
        onCancel={closeEditor}
        onOk={() => form.submit()}
        confirmLoading={saving}
        width={760}
        destroyOnHidden
      >
        <Form key={editingLevel?.level} initialValues={editingLevel} form={form} layout="vertical" onFinish={save}>
          <Form.Item name="model_name" label="模型" rules={[{ required: true, message: "请输入模型名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="base_url" label="服务地址" rules={[{ required: true, type: "url", message: "请输入有效的服务地址" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="api_key" label="API Key" rules={[{ required: true, message: "请输入 API Key" }]}>
            <Input.Password />
          </Form.Item>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Form.Item name="context" label="上下文长度" rules={[{ validator: (_, value: number | undefined) => typeof value === "number" && Number.isInteger(value) && value > 0 ? Promise.resolve() : Promise.reject(new Error("请输入正整数")) }]}>
              <InputNumber min={1} step={1000} precision={0} className="w-full" />
            </Form.Item>
            <Form.Item name="max_tool_rounds" label="最大工具轮数" rules={[{ validator: (_, value: number | null) => isValidToolRoundLimit(value) ? Promise.resolve() : Promise.reject(new Error("请输入正整数或留空")) }]}>
              <InputNumber min={1} className="w-full" placeholder="留空表示不限制" />
            </Form.Item>
          </div>
          <Form.Item name="system_prompt" label="系统提示词" rules={[{ required: true, message: "请输入系统提示词" }]}>
            <PromptEditorField placeholder="该 Level 下所有 Agent 共享的系统提示词。支持 Markdown 结构。" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`复制 Level ${copySource?.level ?? ""} 的配置`}
        open={Boolean(copySource)}
        onCancel={closeCopy}
        onOk={() => void confirmCopy()}
        okText="复制"
        cancelText="取消"
        okButtonProps={{
          disabled: copyTarget === undefined,
          danger: Boolean(levels.find((item) => item.level === copyTarget)?.model_name),
        }}
        confirmLoading={copying}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary">
          将 Level {copySource?.level ?? ""} 的模型、服务地址、API Key、系统提示词、上下文与工具轮数完整写入所选等级；所选等级已有配置会被直接覆盖。
        </Typography.Paragraph>
        <Radio.Group
          value={copyTarget}
          onChange={(event) => setCopyTarget(event.target.value as number)}
          className="w-full"
        >
          <Space orientation="vertical" size={4} className="w-full">
            {copyTargets.map((item) => (
              <Radio key={item.level} value={item.level}>
                Level {item.level}
                {item.model_name ? `（当前：${item.model_name}，将被覆盖）` : "（未配置）"}
              </Radio>
            ))}
          </Space>
        </Radio.Group>
      </Modal>
    </Space>
  );
}
