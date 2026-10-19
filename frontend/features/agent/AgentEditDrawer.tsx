"use client";

import { Alert, App, Button, Drawer, Form, Grid, Input, Modal, Select, Space, Switch, Tabs, Tag, Typography } from "antd";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { isAgentLevel, isSystemAgentModified, unregisteredAgentTools } from "@/domain/agent/agentModel";
import { renderContentHtml } from "@/domain/render/markdown";
import type { AgentConfig, AgentEditValues, AgentPresetRevision, AgentSaveOptions, AgentToolDefinition, LlmLevelConfig, SystemAgentDefinition } from "@/types/agent";
import { AgentTestPanel } from "./components/AgentTestPanel";
import { PresetRevisionList } from "./components/PresetRevisionList";
import { PromptDiffView } from "./components/PromptDiffView";
import { PromptEditorField } from "./components/PromptEditorField";

const nonEmptyTextRule = (message: string) => ({
  validator: (_: unknown, value: string | undefined) => value?.trim() ? Promise.resolve() : Promise.reject(new Error(message)),
});

export type AgentEditDrawerProps = {
  agent?: AgentConfig;
  category: AgentConfig["category"];
  systemAgents: SystemAgentDefinition[];
  tools: AgentToolDefinition[];
  llmLevels: LlmLevelConfig[];
  title?: string;
  open: boolean;
  saving: boolean;
  onClose: () => void;
  onSave: (values: AgentEditValues, options?: AgentSaveOptions) => void;
};

export function AgentEditDrawer(props: AgentEditDrawerProps) {
  const { agent, open, onClose, title } = props;
  const { modal } = App.useApp();
  const screens = Grid.useBreakpoint();
  const heading = title ?? (agent ? `编辑 ${agent.name}` : "新增普通 Agent");
  const [dirty, setDirty] = useState(false);

  function requestClose() {
    if (!dirty || props.saving) {
      onClose();
      return;
    }
    modal.confirm({
      title: "放弃未保存的修改？",
      content: "关闭后本次编辑不会保留，已保存的配置不受影响。",
      okText: "放弃修改",
      okButtonProps: { danger: true },
      cancelText: "继续编辑",
      onOk: () => {
        setDirty(false);
        onClose();
      },
    });
  }

  return (
    <Drawer
      title={<Space size="small"><span>{heading}</span>{props.category === "system" ? <Tag color="geekblue">系统 Agent</Tag> : <Tag color="cyan">普通 Agent</Tag>}</Space>}
      open={open}
      width={screens.md === false ? "100%" : 900}
      maskClosable={false}
      destroyOnHidden
      onClose={requestClose}
    >
      {open ? <AgentEditDrawerBody {...props} onDirtyChange={setDirty} onCancel={requestClose} /> : null}
    </Drawer>
  );
}

type AgentEditDrawerBodyProps = AgentEditDrawerProps & {
  onDirtyChange: (dirty: boolean) => void;
  onCancel: () => void;
};

function AgentEditDrawerBody({ agent, category, systemAgents, tools, llmLevels, saving, onCancel, onSave, onDirtyChange }: AgentEditDrawerBodyProps) {
  const { message } = App.useApp();
  const [form] = Form.useForm<AgentEditValues>();
  const initialValues = useMemo<AgentEditValues>(() => ({
    name: agent?.name ?? "",
    description: agent?.description ?? "",
    prompt: agent?.prompt ?? "",
    level: agent?.level ?? 0,
    capabilities: agent?.capabilities ?? [],
    requiresTool: Boolean(agent?.requiresTool),
  }), [agent]);
  const [draft, setDraft] = useState<AgentEditValues>(initialValues);
  const [activeTab, setActiveTab] = useState("prompt");
  const [defaultPreviewOpen, setDefaultPreviewOpen] = useState(false);
  const [defaultCompareOpen, setDefaultCompareOpen] = useState(false);
  const revisionRef = useRef<AgentSaveOptions | undefined>(undefined);

  const definition = agent?.apid ? systemAgents.find((item) => item.apid === agent.apid) : undefined;
  const isSystem = category === "system";
  const modified = Boolean(agent && definition && isSystemAgentModified(agent, definition));

  const levelOptions = useMemo(() => Array.from({ length: 5 }, (_, level) => {
    const config = llmLevels.find((item) => item.level === level);
    return {
      value: level,
      label: config?.model_name ? `Level ${level} · ${config.model_name}` : `Level ${level}（未配置模型）`,
    };
  }), [llmLevels]);

  // 只提供后端真实注册的工具，避免界面承诺尚未实现的能力。
  const toolOptions = useMemo(
    () => tools.map((tool) => ({
      value: tool.name,
      label: tool.title && tool.title !== tool.name ? `${tool.title}（${tool.name}）` : tool.name,
      title: tool.description,
    })),
    [tools],
  );
  const staleTools = useMemo(
    () => unregisteredAgentTools(draft.capabilities, tools),
    [draft.capabilities, tools],
  );

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        form.submit();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [form]);

  function handleLoadRevision(revision: AgentPresetRevision) {
    const values: AgentEditValues = {
      name: revision.name,
      description: revision.description,
      prompt: revision.prompt,
      level: revision.intelevel,
      capabilities: revision.tools,
      requiresTool: Boolean(revision.requires_tool),
    };
    form.setFieldsValue(values);
    setDraft((current) => ({ ...current, ...values }));
    onDirtyChange(true);
    revisionRef.current = {
      revisionNote: `载入历史版本 ${revision.created_at}`.slice(0, 200),
    };
    setActiveTab("prompt");
  }

  function handleFinish(formValues: AgentEditValues) {
    onSave({
      ...formValues,
      requiresTool: Boolean(formValues.requiresTool && (formValues.capabilities ?? []).length),
    }, revisionRef.current);
  }

  const promptTab = (
    <div className="flex flex-col gap-3">
      {isSystem ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Space size="small">
            {modified ? <Tag color="orange">已修改</Tag> : <Tag color="green">与默认一致</Tag>}
            <Typography.Text type="secondary" className="text-xs">
              系统 Agent 的改动保存后对相关工具立即生效，可随时恢复默认。
            </Typography.Text>
          </Space>
          <Space size="small">
            <Button size="small" onClick={() => setDefaultPreviewOpen(true)}>查看默认</Button>
            <Button size="small" onClick={() => setDefaultCompareOpen(true)}>与默认对比</Button>
          </Space>
        </div>
      ) : null}
      <Form.Item
        name="prompt"
        label="提示词"
        rules={[nonEmptyTextRule("请输入提示词")]}
        className="!mb-0"
      >
        <PromptEditorField placeholder="描述此 Agent 的角色、任务、约束与输出要求，支持 Markdown 结构。" />
      </Form.Item>
    </div>
  );

  const tabs: { key: string; label: string; children: ReactNode }[] = [
    { key: "prompt", label: "提示词", children: promptTab },
    {
      key: "history",
      label: "历史版本",
      children: <PresetRevisionList apid={agent?.apid} currentPrompt={draft.prompt ?? ""} onLoad={handleLoadRevision} />,
    },
    {
      key: "test",
      label: "试运行",
      children: <AgentTestPanel agent={agent} values={draft} />,
    },
  ];

  return (
    <>
      <Form
        form={form}
        layout="vertical"
        initialValues={initialValues}
        onValuesChange={(_changed, all) => {
          setDraft(all);
          onDirtyChange(true);
        }}
        onFinish={handleFinish}
        onFinishFailed={() => message.warning("请先修正表单中的错误项")}
      >
        <div className="grid grid-cols-1 gap-x-4 md:grid-cols-2">
          <Form.Item name="name" label="名称" rules={[nonEmptyTextRule("请输入名称")]}>
            <Input placeholder="例如：报价跟进助手" />
          </Form.Item>
          <Form.Item
            name="level"
            label="等级"
            rules={[{ validator: (_, value: number | undefined) => isAgentLevel(Number(value)) ? Promise.resolve() : Promise.reject(new Error("等级必须是 0 到 4 的整数")) }]}
          >
            <Select options={levelOptions} />
          </Form.Item>
          <Form.Item name="description" label="描述" className="md:col-span-2">
            <Input.TextArea rows={2} placeholder="一句话说明这个 Agent 负责什么。" />
          </Form.Item>
          {category === "regular" ? (
            <Form.Item name="capabilities" label="能力" className="md:col-span-2" extra="只列出后端已注册的工具；新增工具需先在运行时装好实现。">
              <Select mode="multiple" options={toolOptions} placeholder="选择该 Agent 可调用的工具" />
            </Form.Item>
          ) : null}
          <Form.Item
            name="requiresTool"
            label="必须调用工具"
            valuePropName="checked"
            className="md:col-span-2"
            extra="开启后，模型必须至少调用一次工具；达到调用上限时会强制收尾，并保留已发生的调用记录。"
          >
            <Switch disabled={(draft.capabilities ?? []).length === 0 && !draft.requiresTool} />
          </Form.Item>
          {staleTools.length > 0 ? (
            <Alert
              className="md:col-span-2"
              type="warning"
              showIcon
              message={`以下工具未注册，该 Agent 无法运行：${staleTools.join("、")}`}
              description="请从「能力」中移除它们后再保存或试运行。"
            />
          ) : null}
        </div>

        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabs} />

        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <Space>
            <Button onClick={onCancel}>取消</Button>
            <Button type="primary" htmlType="submit" loading={saving}>保存</Button>
          </Space>
          <Typography.Text type="secondary" className="text-xs">Ctrl/Cmd + S 保存；有未保存修改时关闭会二次确认。</Typography.Text>
        </div>
      </Form>

      <Modal
        open={defaultPreviewOpen}
        title={`${definition?.display_name ?? "系统 Agent"} · 内置默认提示词`}
        footer={null}
        width={760}
        destroyOnHidden
        onCancel={() => setDefaultPreviewOpen(false)}
      >
        <div
          aria-label="默认提示词预览"
          className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm"
          dangerouslySetInnerHTML={renderContentHtml(definition?.prompt ?? "", "markdown")}
        />
      </Modal>

      <Modal
        open={defaultCompareOpen}
        title="与内置默认对比"
        footer={null}
        width={960}
        destroyOnHidden
        onCancel={() => setDefaultCompareOpen(false)}
      >
        <PromptDiffView before={definition?.prompt ?? ""} after={draft.prompt ?? ""} maxHeight={520} />
      </Modal>
    </>
  );
}
