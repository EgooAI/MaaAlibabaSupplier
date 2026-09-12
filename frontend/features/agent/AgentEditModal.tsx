"use client";

import { Button, Form, Input, InputNumber, Modal, Select, Space } from "antd";
import { AGENT_TOOL_OPTIONS, isAgentLevel } from "@/domain/agent/agentModel";
import type { AgentConfig, AgentEditValues } from "@/types/agent";

const nonEmptyTextRule = (message: string) => ({
  validator: (_: unknown, value: string | undefined) => value?.trim() ? Promise.resolve() : Promise.reject(new Error(message)),
});

type AgentEditModalProps = {
  agent?: AgentConfig;
  category?: AgentConfig["category"];
  title?: string;
  open: boolean;
  saving: boolean;
  onClose: () => void;
  onSave: (values: AgentEditValues) => void;
};

export function AgentEditModal({ agent, category, title, open, saving, onClose, onSave }: AgentEditModalProps) {
  // destroyOnHidden 会在每次打开时重挂 Form，initialValues + key 保证初值正确，无需 effect 同步。
  const initialValues: AgentEditValues = {
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    prompt: agent?.prompt ?? "",
    level: agent?.level ?? 0,
    capabilities: agent?.capabilities ?? [],
  };

  return (
    <Modal
      title={title ?? `编辑 ${agent?.name ?? "Agent"}`}
      open={open}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
    >
      <Form key={agent?.id ?? "new"} initialValues={initialValues} layout="vertical" onFinish={onSave}>
        <Form.Item name="name" label="名称" rules={[nonEmptyTextRule("请输入名称")]}>
          <Input />
        </Form.Item>
        <Form.Item name="description" label="描述">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Form.Item name="prompt" label="提示词" rules={[nonEmptyTextRule("请输入提示词")]}>
          <Input.TextArea rows={4} />
        </Form.Item>
        <Form.Item name="level" label="等级" rules={[{ validator: (_, value: number | undefined) => isAgentLevel(Number(value)) ? Promise.resolve() : Promise.reject(new Error("等级必须是 0 到 4 的整数")) }]}>
          <InputNumber min={0} max={4} step={1} precision={0} className="w-full" />
        </Form.Item>
        {(agent?.category ?? category) === "regular" && (
          <Form.Item name="capabilities" label="能力">
            <Select mode="tags" tokenSeparators={[",", "，"]} options={[...AGENT_TOOL_OPTIONS]} />
          </Form.Item>
        )}
        <div className="flex justify-end gap-2">
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" loading={saving} htmlType="submit">保存</Button>
          </Space>
        </div>
      </Form>
    </Modal>
  );
}
