"use client";

import { EditOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, InputNumber, Modal, Space, Table, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { isValidToolRoundLimit } from "@/domain/agent/agentModel";
import type { LlmLevelConfig } from "@/types/agent";
import { HydrationSafeTable } from "@/components/HydrationSafeTable";
import { useAgentWorkbench } from "./hooks/useAgentWorkbench";

type LlmLevelFormValues = Omit<LlmLevelConfig, "level">;

export function LlmPage() {
  const workbench = useAgentWorkbench();
  const [editingLevel, setEditingLevel] = useState<LlmLevelConfig>();
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<LlmLevelFormValues>();

  const levels = useMemo(() => workbench.state?.llmLevels ?? [], [workbench.state?.llmLevels]);

  useEffect(() => {
    if (editingLevel) form.setFieldsValue(editingLevel);
  }, [editingLevel, form]);

  function closeEditor() {
    setEditingLevel(undefined);
    form.resetFields();
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
      <div>
        <Typography.Title level={2} className="!mb-1">LLM</Typography.Title>
      </div>
      <Card title={<span>LLM Level 配置 <Tag color="blue">{levels.length} 个层级</Tag></span>} loading={workbench.loading}>
        <Table
          rowKey="level"
          dataSource={levels}
          scroll={{ x: 1340 }}
          components={{ table: HydrationSafeTable }}
          columns={[
            {
              title: "Level",
              dataIndex: "level",
              width: 100,
              render: (value: number) => <Tag color={value >= 3 ? "blue" : "default"}>L{value}</Tag>,
            },
            { title: "模型", dataIndex: "modelName", width: 240, ellipsis: true },
            { title: "服务地址", dataIndex: "baseUrl", width: 280, ellipsis: true },
            { title: "系统提示词", dataIndex: "systemPrompt", width: 360, ellipsis: true },
            {
              title: "上下文",
              dataIndex: "context",
              width: 150,
              align: "right" as const,
              render: (value: number) => `${value.toLocaleString()} tokens`,
            },
            { title: "最大工具轮数", dataIndex: "maxToolRounds", width: 150, align: "right" as const, render: (value: number | null) => value ?? "不限" },
            { title: "操作", width: 110, fixed: "right" as const, render: (_: unknown, record: LlmLevelConfig) => <Button icon={<EditOutlined />} onClick={() => setEditingLevel(record)}>编辑</Button> },
          ]}
          locale={{ emptyText: "暂无 LLM 配置" }}
        />
      </Card>

      <Modal
        title={`编辑 Level ${editingLevel?.level ?? ""}`}
        open={Boolean(editingLevel)}
        onCancel={closeEditor}
        onOk={() => form.submit()}
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={save}>
          <Form.Item name="modelName" label="模型" rules={[{ required: true, message: "请输入模型名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="baseUrl" label="服务地址" rules={[{ required: true, type: "url", message: "请输入有效的服务地址" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="apiKey" label="API Key" rules={[{ required: true, message: "请输入 API Key" }]}>
            <Input.Password />
          </Form.Item>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Form.Item name="context" label="上下文长度" rules={[{ validator: (_, value: number | undefined) => typeof value === "number" && Number.isInteger(value) && value > 0 ? Promise.resolve() : Promise.reject(new Error("请输入正整数")) }]}>
              <InputNumber min={1} step={1000} precision={0} className="w-full" />
            </Form.Item>
            <Form.Item name="maxToolRounds" label="最大工具轮数" rules={[{ validator: (_, value: number | null) => isValidToolRoundLimit(value) ? Promise.resolve() : Promise.reject(new Error("请输入正整数或留空")) }]}>
              <InputNumber min={1} className="w-full" placeholder="留空表示不限制" />
            </Form.Item>
          </div>
          <Form.Item name="systemPrompt" label="系统提示词" rules={[{ required: true, message: "请输入系统提示词" }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
