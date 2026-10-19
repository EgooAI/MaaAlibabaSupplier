"use client";

import { Alert, App, Button, Drawer, Form, Select, Space, Tag, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { backend } from "@/services/client";
import type { AgentConfig, AgentPresetRevision, AgentTestSession } from "@/types/agent";
import type { ConversationDetail } from "@/types/chatCanonical";

type ForkValues = {
  agentId?: string;
  revisionId?: number | null;
};

/**
 * "从此开始模拟": pick an Agent and version, freeze the conversation up to the
 * chosen message, and open the resulting simulated session in the Playground.
 */
export function ForkSessionDrawer({
  conversation,
  open,
  messageIndex,
  onClose,
}: {
  conversation?: ConversationDetail;
  open: boolean;
  messageIndex?: number;
  onClose: () => void;
}) {
  const router = useRouter();
  const { message } = App.useApp();
  const [form] = Form.useForm<ForkValues>();
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [revisionsByAgent, setRevisionsByAgent] = useState<Record<string, AgentPresetRevision[]>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();

  const agentId = Form.useWatch("agentId", form);
  const revisions = useMemo(() => (agentId ? revisionsByAgent[agentId] ?? [] : []), [agentId, revisionsByAgent]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    backend.getAgentConsole()
      .then((state) => { if (!cancelled) setAgents(state.agents ?? []); })
      .catch(() => { if (!cancelled) setError("Agent 列表加载失败"); });
    return () => { cancelled = true; };
  }, [open]);

  useEffect(() => {
    if (!open || !agentId || revisionsByAgent[agentId]) return;
    let cancelled = false;
    backend.listAgentPresetRevisions(agentId)
      .then((items) => {
        if (!cancelled) setRevisionsByAgent((current) => ({ ...current, [agentId]: items }));
      })
      .catch(() => {
        if (!cancelled) setRevisionsByAgent((current) => ({ ...current, [agentId]: [] }));
      });
    return () => { cancelled = true; };
  }, [agentId, open, revisionsByAgent]);

  async function submit(values: ForkValues) {
    if (!conversation || messageIndex === undefined) return;
    setSubmitting(true);
    setError(undefined);
    try {
      const session: AgentTestSession = await backend.forkAgentSession({
        agentId: String(values.agentId),
        conversationId: Number(conversation.id),
        messageIndex,
        revisionId: values.revisionId ?? null,
      });
      onClose();
      router.push(`/agent/playground?session=${encodeURIComponent(String(session.id))}`);
    } catch (caught: unknown) {
      const text = caught instanceof Error ? caught.message : "模拟会话创建失败";
      setError(text);
      message.error(text);
    } finally {
      setSubmitting(false);
    }
  }

  const preview = conversation && messageIndex !== undefined ? conversation.messages[messageIndex] : undefined;

  return (
    <Drawer
      title="从此开始模拟"
      open={open}
      onClose={onClose}
      width={460}
      destroyOnHidden
      afterOpenChange={(visible) => {
        if (!visible) return;
        // Reset per opening here instead of in an effect body.
        setError(undefined);
         form.setFieldsValue({ agentId: undefined, revisionId: null });
      }}
    >
       <Form form={form} layout="vertical" onFinish={submit}>
        <Typography.Paragraph type="secondary">
          选中的对话会按正式运行时的 Markdown 投影冻结为输入；之后在这个模拟会话里扮演买家继续对话，不会发送给客户。
        </Typography.Paragraph>
        {error ? <Alert type="warning" showIcon message={error} className="mb-3" /> : null}
        <Form.Item name="agentId" label="Agent" rules={[{ required: true, message: "请选择 Agent" }]}>
          <Select
            placeholder="选择接手这个对话的 Agent"
            options={agents.map((agent) => ({ value: agent.id, label: agent.name, disabled: !agent.enabled }))}
            onChange={() => form.setFieldsValue({ revisionId: null })}
          />
        </Form.Item>
        <Form.Item name="revisionId" label="历史版本" extra="留空表示使用当前保存的配置。">
          <Select
            allowClear
            placeholder="当前保存的配置"
            options={revisions.map((revision) => ({
              value: revision.id,
              label: `${revision.created_at} · ${revision.prompt.slice(0, 24)}`,
            }))}
          />
        </Form.Item>
        {preview ? (
          <div className="mb-3 rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
            <Space size={4} className="mb-1">
              <Tag>消息 #{messageIndex! + 1}</Tag>
              <span>{preview.createdAt}</span>
            </Space>
            <div className="line-clamp-4 whitespace-pre-wrap break-words">{preview.content}</div>
          </div>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" htmlType="submit" loading={submitting} disabled={!conversation}>开始模拟</Button>
        </div>
      </Form>
    </Drawer>
  );
}
