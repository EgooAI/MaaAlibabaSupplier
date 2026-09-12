"use client";

import { SaveOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, Select, Space } from "antd";
import { useEffect, useMemo, useState } from "react";
import type { LlmLevelConfig } from "@/types/agent";
import { useAgentWorkbench } from "./hooks/useAgentWorkbench";

type LevelPromptFormValues = {
  level: number;
  systemPrompt: string;
};

export function SystemPromptPage() {
  const workbench = useAgentWorkbench();
  const [form] = Form.useForm<LevelPromptFormValues>();
  const [saving, setSaving] = useState(false);
  const levels = useMemo(() => workbench.state?.llmLevels ?? [], [workbench.state?.llmLevels]);
  const selectedLevel = Form.useWatch("level", form);
  const selectedConfig = levels.find((level) => level.level === selectedLevel);
  const canSave = Boolean(selectedConfig);

  useEffect(() => {
    if (!levels.length) return;
    const currentLevel = form.getFieldValue("level") as number | undefined;
    if (levels.some((level) => level.level === currentLevel)) return;
    const initial = levels[0];
    form.setFieldsValue({ level: initial.level, systemPrompt: initial.systemPrompt });
  }, [form, levels]);

  function selectLevel(level: number) {
    const config = levels.find((item) => item.level === level);
    if (config) form.setFieldsValue({ level, systemPrompt: config.systemPrompt });
  }

  async function save({ level, systemPrompt }: LevelPromptFormValues) {
    const targetConfig = levels.find((config) => config.level === level);
    if (!targetConfig) return;
    setSaving(true);
    try {
      const nextConfig: LlmLevelConfig = { ...targetConfig, systemPrompt };
      await workbench.saveLlmLevel(nextConfig);
    } finally {
      setSaving(false);
    }
  }

  return (
      <Space orientation="vertical" size="large" className="w-full">
      <Card loading={workbench.loading}>
        <Form form={form} layout="vertical" onFinish={save}>
          <Form.Item name="level" label="Level" rules={[{ required: true, message: "请选择 Level" }]}>
            <Select options={levels.map((level) => ({ label: `Level ${level.level}`, value: level.level }))} onChange={selectLevel} />
          </Form.Item>
          <Form.Item name="systemPrompt" label="SYSTEM_PROMPT" rules={[{ required: true, message: "请输入 SYSTEM_PROMPT" }]}>
            <Input.TextArea autoSize={{ minRows: 3, maxRows: 8 }} />
          </Form.Item>
          <Button type="primary" icon={<SaveOutlined />} htmlType="submit" loading={saving} disabled={!canSave}>
            保存 Level 提示词
          </Button>
        </Form>
      </Card>
    </Space>
  );
}
