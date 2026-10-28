"use client";

import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, App, Button, Input, Modal, Select, Space, Tag, Tooltip, Typography } from "antd";
import { useEffect, useMemo, useState, type ReactElement } from "react";
import { backend } from "@/services/client";
import type { AgentConfig, AgentPresetRevision, AgentTestDraftInput } from "@/types/agent";
import type { CreateExperimentSourceInput, Experiment, ExperimentVariantInput } from "@/types/experiment";

/** Mirrors the backend's MAX_VARIANTS_PER_EXPERIMENT. */
const MAX_VARIANTS = 4;

type VersionKind = "preset" | "revision" | "draft";

type VariantRow = {
  key: string;
  label: string;
  kind: VersionKind;
  revisionId?: number;
};

export type ExperimentCreateModalProps = {
  open: boolean;
  onClose: () => void;
  onCreated: (experiment: Experiment) => void;
  initialAgentId?: string;
  source?: CreateExperimentSourceInput;
  initialDraft?: AgentTestDraftInput;
  /**
   * Additive, optional: preselect a historical revision for the second variant.
   * The fork drawer passes the revision the user just picked.
   */
  initialRevisionId?: number;
  /** Additive, optional: a readable title for the conversation starting point. */
  conversationTitle?: string;
};

const VERSION_KIND_LABELS: Record<VersionKind, string> = {
  preset: "当前保存配置",
  revision: "历史版本",
  draft: "编辑器草稿",
};

let rowSequence = 0;

function nextRowKey() {
  rowSequence += 1;
  return `variant-${rowSequence}`;
}

/**
 * Creates one experiment: pick the Agent, describe 1-4 versions to compare and
 * the frozen starting point. The first variant is the baseline.
 */
export function ExperimentCreateModal({
  open,
  onClose,
  onCreated,
  initialAgentId,
  source,
  initialDraft,
  initialRevisionId,
  conversationTitle,
}: ExperimentCreateModalProps): ReactElement {
  return (
    <Modal
      title="创建对比实验"
      open={open}
      width={640}
      destroyOnHidden
      maskClosable={false}
      footer={null}
      onCancel={onClose}
    >
      {/* destroyOnHidden remounts the body, so every opening starts clean. */}
      <ExperimentCreateBody
        initialAgentId={initialAgentId}
        source={source}
        initialDraft={initialDraft}
        initialRevisionId={initialRevisionId}
        conversationTitle={conversationTitle}
        onClose={onClose}
        onCreated={onCreated}
      />
    </Modal>
  );
}

type BodyProps = {
  initialAgentId?: string;
  source?: CreateExperimentSourceInput;
  initialDraft?: AgentTestDraftInput;
  initialRevisionId?: number;
  conversationTitle?: string;
  onClose: () => void;
  onCreated: (experiment: Experiment) => void;
};

function ExperimentCreateBody({
  initialAgentId,
  source,
  initialDraft,
  initialRevisionId,
  conversationTitle,
  onClose,
  onCreated,
}: BodyProps) {
  const { message } = App.useApp();
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [agentsError, setAgentsError] = useState<string>();
  const [agentId, setAgentId] = useState(initialAgentId ?? "");
  const [name, setName] = useState("");
  const [text, setText] = useState(source?.kind === "text" ? source.text ?? "" : "");
  const [rows, setRows] = useState<VariantRow[]>(() => initialRows(initialDraft, initialRevisionId));
  const [revisionsByAgent, setRevisionsByAgent] = useState<Record<string, AgentPresetRevision[]>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();

  // The first enabled agent is the default only when nothing was preselected.
  useEffect(() => {
    let cancelled = false;
    backend.getAgentConsole()
      .then((state) => {
        if (cancelled) return;
        const list = state.agents ?? [];
        setAgents(list);
        setAgentId((current) => current || list.find((agent) => agent.enabled)?.id || "");
      })
      .catch(() => { if (!cancelled) setAgentsError("Agent 列表加载失败"); });
    return () => { cancelled = true; };
  }, []);

  // Revisions are loaded on demand, once per Agent.
  const revisionsLoaded = Boolean(agentId && revisionsByAgent[agentId]);
  const needsRevisions = rows.some((row) => row.kind === "revision");
  useEffect(() => {
    if (!agentId || !needsRevisions || revisionsLoaded) return;
    let cancelled = false;
    backend.listAgentPresetRevisions(agentId)
      .then((items) => {
        if (!cancelled) setRevisionsByAgent((current) => ({ ...current, [agentId]: items }));
      })
      .catch(() => {
        if (cancelled) return;
        setRevisionsByAgent((current) => ({ ...current, [agentId]: [] }));
        message.warning("历史版本加载失败");
      });
    return () => { cancelled = true; };
  }, [agentId, needsRevisions, revisionsLoaded, message]);

  const revisions = useMemo(() => (agentId ? revisionsByAgent[agentId] ?? [] : []), [agentId, revisionsByAgent]);

  function changeAgent(nextAgentId: string) {
    setAgentId(nextAgentId);
    // Revisions belong to one Agent: selections cannot survive a switch.
    setRows((current) => current.map((row) => row.kind === "revision" ? { ...row, kind: "preset", revisionId: undefined } : row));
  }

  function updateRow(key: string, patch: Partial<VariantRow>) {
    setRows((current) => current.map((row) => row.key === key ? { ...row, ...patch } : row));
  }

  function changeRowKind(key: string, kind: VersionKind) {
    setRows((current) => current.map((row) => {
      if (row.key !== key) {
        // The editor draft can be pinned only once: it is the unsaved buffer itself.
        return kind === "draft" && row.kind === "draft" ? { ...row, kind: "preset", revisionId: undefined } : row;
      }
      return { ...row, kind, revisionId: undefined };
    }));
  }

  function addRow() {
    setRows((current) => [...current, { key: nextRowKey(), label: "", kind: "preset" }]);
  }

  function removeRow(key: string) {
    setRows((current) => current.length <= 1 ? current : current.filter((row) => row.key !== key));
  }

  async function submit() {
    if (!agentId) {
      setError("请选择 Agent");
      return;
    }
    if (rows.some((row) => row.kind === "revision" && row.revisionId === undefined)) {
      setError("请为「历史版本」选择具体版本");
      return;
    }
    setSubmitting(true);
    setError(undefined);
    try {
      const experiment = await backend.createExperiment({
        agentId,
        ...(name.trim() ? { name: name.trim() } : {}),
        source: resolveSource(source, text),
        variants: rows.map((row) => toVariantInput(row, initialDraft)),
      });
      message.success("实验已创建");
      onCreated(experiment);
      onClose();
    } catch (caught: unknown) {
      const detail = caught instanceof Error ? caught.message : "实验创建失败";
      setError(detail);
      message.error(detail);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <Typography.Paragraph type="secondary" className="!mb-0">
        一个实验会用同一起点运行多个版本；第一个变体是基线。每次运行后可以逐条对比它们的回复。
      </Typography.Paragraph>

      {source ? <SourceSummary source={source} title={conversationTitle} /> : null}
      {source?.kind === "text" || !source ? (
        <Input.TextArea
          rows={4}
          value={text}
          placeholder="粘贴客户消息或对话摘要，作为所有变体的冻结输入。"
          onChange={(event) => setText(event.target.value)}
        />
      ) : null}

      {agentsError ? <Alert type="warning" showIcon message={agentsError} /> : null}
      {error ? <Alert type="warning" showIcon message={error} /> : null}

      <div className="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span>Agent</span>
          <Select
            value={agentId || undefined}
            placeholder="选择要对比的 Agent"
            aria-label="Agent"
            options={agents.map((agent) => ({ value: agent.id, label: agent.name, disabled: !agent.enabled }))}
            onChange={changeAgent}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span>实验名称</span>
          <Input value={name} placeholder="留空则使用「Agent 名 对比实验」" onChange={(event) => setName(event.target.value)} />
        </label>
      </div>

      <div className="flex flex-col gap-2" data-testid="variant-rows">
        <div className="flex items-center justify-between">
          <Typography.Text strong>对比的版本</Typography.Text>
          <Typography.Text type="secondary" className="text-xs">第 1 行是基线；最多 {MAX_VARIANTS} 个。</Typography.Text>
        </div>
        {rows.map((row, index) => (
          <div
            key={row.key}
            data-testid="variant-row"
            className="flex flex-col gap-2 rounded border border-slate-200 p-2 sm:flex-row sm:items-center"
          >
            <Input
              className="sm:w-44"
              value={row.label}
              placeholder={index === 0 ? "基线" : "更简洁的版本"}
              aria-label={`变体 ${index + 1} 标签`}
              onChange={(event) => updateRow(row.key, { label: event.target.value })}
            />
            <Select
              className="sm:w-40"
              value={row.kind}
              aria-label={`变体 ${index + 1} 版本来源`}
              options={kindOptions(Boolean(initialDraft))}
              onChange={(kind: VersionKind) => changeRowKind(row.key, kind)}
            />
            {row.kind === "revision" ? (
              <Select
                className="sm:flex-1"
                value={row.revisionId}
                placeholder="选择历史版本"
                aria-label={`变体 ${index + 1} 历史版本`}
                loading={!revisionsLoaded}
                notFoundContent="暂无历史版本"
                options={revisions.map((revision) => ({
                  value: revision.id,
                  label: `${revision.created_at} · ${revision.prompt.slice(0, 20)}`,
                }))}
                onChange={(revisionId: number) => updateRow(row.key, { revisionId })}
              />
            ) : null}
            <Tooltip title={rows.length <= 1 ? "至少保留一个变体" : "删除这个变体"}>
              <Button
                aria-label={`删除变体 ${index + 1}`}
                icon={<DeleteOutlined />}
                disabled={rows.length <= 1}
                onClick={() => removeRow(row.key)}
              />
            </Tooltip>
          </div>
        ))}
        <div>
          <Button size="small" icon={<PlusOutlined />} disabled={rows.length >= MAX_VARIANTS} onClick={addRow}>
            添加版本
          </Button>
        </div>
      </div>

      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>取消</Button>
        <Button type="primary" loading={submitting} disabled={!agentId} onClick={() => void submit()}>
          创建实验并开始对比
        </Button>
      </div>
    </div>
  );
}

function initialRows(initialDraft?: AgentTestDraftInput, initialRevisionId?: number): VariantRow[] {
  const baseline: VariantRow = { key: nextRowKey(), label: "", kind: "preset" };
  if (initialDraft) {
    return [baseline, { key: nextRowKey(), label: "", kind: "draft" }];
  }
  if (initialRevisionId !== undefined) {
    return [baseline, { key: nextRowKey(), label: "", kind: "revision", revisionId: initialRevisionId }];
  }
  return [baseline, { key: nextRowKey(), label: "", kind: "preset" }];
}

function kindOptions(withDraft: boolean) {
  const kinds: VersionKind[] = withDraft ? ["preset", "revision", "draft"] : ["preset", "revision"];
  return kinds.map((kind) => ({ value: kind, label: VERSION_KIND_LABELS[kind] }));
}

function toVariantInput(row: VariantRow, draft: AgentTestDraftInput | undefined): ExperimentVariantInput {
  // A blank label lets the backend name it: "变体 1", "变体 2"...
  const label = row.label.trim() || undefined;
  if (row.kind === "revision") {
    return { label, versionKind: "preset", revisionId: row.revisionId ?? null };
  }
  if (row.kind === "draft") {
    // The draft row exists only when the editor supplied one; a stray draft row
    // falls back to the saved preset rather than sending an empty prompt.
    return draft ? { label, versionKind: "draft", draft } : { label, versionKind: "preset" };
  }
  return { label, versionKind: "preset" };
}

function resolveSource(source: CreateExperimentSourceInput | undefined, text: string): CreateExperimentSourceInput {
  if (!source) return { kind: "text", text };
  if (source.kind === "text") return { ...source, text };
  return source;
}

function SourceSummary({ source, title }: { source: CreateExperimentSourceInput; title?: string }) {
  if (source.kind === "conversation") {
    const heading = title ? `从对话「${title}」` : `从对话 #${source.conversationId ?? "?"}`;
    return (
      <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
        <Space size={4} wrap>
          <Tag>{heading}</Tag>
          <span>第 {(source.messageIndex ?? 0) + 1} 条消息开始</span>
        </Space>
      </div>
    );
  }
  if (source.kind === "session") {
    return (
      <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
        <Tag>基于会话</Tag>
        <span>{source.sessionId}</span>
      </div>
    );
  }
  return (
    <div className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
      <Tag>粘贴文本</Tag>
      <span>下方文本会作为所有变体的冻结起点。</span>
    </div>
  );
}
