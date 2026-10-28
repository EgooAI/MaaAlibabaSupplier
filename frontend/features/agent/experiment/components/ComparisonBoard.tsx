"use client";

import { CheckCircleFilled, DownOutlined, MinusCircleOutlined } from "@ant-design/icons";
import { Button, Dropdown, Empty, Grid, Input, Progress, Space, Tabs, Tag, Tooltip, Typography } from "antd";
import { useState } from "react";
import type { AgentTestSession } from "@/types/agent";
import type { Experiment, ExperimentAnnotation, ExperimentStep, ExperimentVariant, RunSnapshot, VariantRunStatus } from "@/types/experiment";
import { ToolCallTimeline } from "../../timeline/ToolCallTimeline";
import { locateStepTurns, orderedSteps, stepDoneFor } from "../stepTurns";

type ComparisonBoardProps = {
  experiment: Experiment;
  run?: RunSnapshot;
  runningStepIndex?: number;
  disabled?: boolean;
  onAnnotate: (annotations: ExperimentAnnotation[]) => void | Promise<unknown>;
  onPromote: (variantId: string) => void | Promise<unknown>;
  onRemoveVariant: (variantId: string) => void | Promise<unknown>;
  onRetryStep: (stepIndex: number) => void | Promise<unknown>;
};

/** One variant's status inside the run for the step currently on screen. */
function statusOf(run: RunSnapshot | undefined, stepIndex: number, variantId: string): VariantRunStatus | undefined {
  if (!run || run.stepIndex !== stepIndex) return undefined;
  return run.variants.find((item) => item.variantId === variantId);
}

export function ComparisonBoard({
  experiment,
  run,
  runningStepIndex,
  disabled,
  onAnnotate,
  onPromote,
  onRemoveVariant,
  onRetryStep,
}: ComparisonBoardProps) {
  const screens = Grid.useBreakpoint();
  // Narrow screens get one variant at a time: four 320px columns would only
  // produce horizontal hunting.
  const narrow = screens.lg === false;
  const [selectedVariant, setSelectedVariant] = useState<string>();
  const variants = experiment.variants;
  const steps = orderedSteps(experiment);
  const activeVariant = variants.some((variant) => variant.id === selectedVariant)
    ? selectedVariant
    : variants[0]?.id;

  function annotationFor(stepIndex: number, variantId: string) {
    return experiment.annotations.find((item) => item.step === stepIndex && item.variantId === variantId);
  }

  function saveAnnotation(stepIndex: number, variantId: string, patch: Partial<Pick<ExperimentAnnotation, "verdict" | "note">>) {
    const current = annotationFor(stepIndex, variantId);
    const next: ExperimentAnnotation = {
      step: stepIndex,
      variantId,
      verdict: patch.verdict ?? current?.verdict ?? "",
      note: patch.note ?? current?.note ?? "",
    };
    const rest = experiment.annotations.filter((item) => !(item.step === stepIndex && item.variantId === variantId));
    // An empty verdict with an empty note is not worth storing.
    void onAnnotate(next.verdict || next.note.trim() ? [...rest, next] : rest);
  }

  if (!variants.length) {
    return <Empty description="这个实验还没有变体：请在创建时选择 1 到 4 个版本。" />;
  }

  const header = (variant: ExperimentVariant) => (
    <VariantHeader
      key={variant.id}
      variant={variant}
      disabled={disabled}
      onPromote={() => void onPromote(variant.id)}
      onRemove={() => void onRemoveVariant(variant.id)}
    />
  );

  const cell = (step: ExperimentStep, variant: ExperimentVariant) => (
    <StepCell
      key={`${step.index}:${variant.id}`}
      step={step}
      variant={variant}
      experiment={experiment}
      status={statusOf(run, step.index, variant.id)}
      annotation={annotationFor(step.index, variant.id)}
      running={runningStepIndex === step.index}
      disabled={disabled}
      onVerdict={(verdict) => saveAnnotation(step.index, variant.id, { verdict })}
      onNote={(note) => saveAnnotation(step.index, variant.id, { note })}
      onRetry={() => void onRetryStep(step.index)}
    />
  );

  if (narrow) {
    return (
      <Tabs
        activeKey={activeVariant}
        onChange={setSelectedVariant}
        items={variants.map((variant) => ({
          key: variant.id,
          label: variant.label,
          children: (
            <div className="flex flex-col gap-3">
              {header(variant)}
              {steps.length ? (
                steps.map((step) => (
                  <div key={step.index} className="flex flex-col gap-1">
                    <StepAxis step={step} experiment={experiment} />
                    {cell(step, variant)}
                  </div>
                ))
              ) : (
                <Typography.Text type="secondary">还没有步骤：在下方输入第一条买家消息。</Typography.Text>
              )}
            </div>
          ),
        }))}
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <div
        className="grid min-w-max gap-2"
        style={{ gridTemplateColumns: `minmax(160px, 200px) repeat(${variants.length}, minmax(320px, 1fr))` }}
      >
        <div />
        {variants.map(header)}
        {steps.map((step) => (
          <div key={step.index} className="contents">
            <div className="flex flex-col gap-1 py-2">
              <StepAxis step={step} experiment={experiment} />
            </div>
            {variants.map((variant) => cell(step, variant))}
          </div>
        ))}
      </div>
      {steps.length ? null : (
        <Empty className="mt-4" description="还没有步骤：在下方输入第一条买家消息。" />
      )}
    </div>
  );
}

function VariantHeader({
  variant,
  disabled,
  onPromote,
  onRemove,
}: {
  variant: ExperimentVariant;
  disabled?: boolean;
  onPromote: () => void;
  onRemove: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const pinned = variant.pinned;
  const label = pinned?.kind === "draft" ? "草稿快照" : "已保存版本";
  const revision = pinned?.revision_id ?? "当前配置";
  const tools = pinned?.tools ?? [];

  return (
    <div className="flex flex-col gap-1 rounded border border-slate-200 bg-slate-50 p-2">
      <div className="flex items-start justify-between gap-2">
        <Typography.Text strong className="min-w-0 truncate">{variant.label}</Typography.Text>
        <Dropdown
          trigger={["click"]}
          disabled={disabled}
          menu={{
            items: [
              { key: "promote", label: "保存为当前版本" },
              { key: "remove", label: "移除此变体" },
            ],
            onClick: ({ key }) => {
              if (key === "promote") onPromote();
              if (key === "remove") setConfirming(true);
            },
          }}
        >
          <Button size="small" type="text" icon={<DownOutlined />} aria-label={`${variant.label} 的操作`} />
        </Dropdown>
      </div>
      <Space size={4} wrap>
        <Tag color={pinned?.kind === "draft" ? "orange" : "default"}>{label}</Tag>
        <Typography.Text type="secondary" className="text-[11px]">{revision}</Typography.Text>
        <Typography.Text type="secondary" className="text-[11px]">Level {pinned?.llm_level ?? 0}</Typography.Text>
        <Tooltip title={tools.length ? tools.join("、") : "未挂载工具"}>
          <Typography.Text type="secondary" className="text-[11px]">{tools.length} 个工具</Typography.Text>
        </Tooltip>
      </Space>
      {confirming ? (
        <Space size={4} wrap>
          <Typography.Text type="danger" className="text-[11px]">移除后该变体的对话记录一并删除。</Typography.Text>
          <Button size="small" danger onClick={onRemove}>确认移除</Button>
          <Button size="small" type="text" onClick={() => setConfirming(false)}>取消</Button>
        </Space>
      ) : null}
    </div>
  );
}

function StepAxis({ step, experiment }: { step: ExperimentStep; experiment: Experiment }) {
  const done = stepDoneFor(experiment, step.index, experiment.variants[0]?.id ?? "");
  const completed = experiment.progress[String(step.index)]?.variants?.length ?? 0;
  return (
    <div className="flex flex-col gap-1">
      <Space size={4}>
        <Typography.Text strong className="text-xs">第 {step.index + 1} 步</Typography.Text>
        <Tag color={step.mode === "buyer" ? "blue" : "purple"}>{step.mode === "buyer" ? "买家" : "Agent"}</Tag>
        {done ? <CheckCircleFilled className="text-green-500" /> : null}
      </Space>
      <Typography.Paragraph type="secondary" className="!mb-0 text-[11px]" ellipsis={{ rows: 3, tooltip: step.text }}>
        {step.mode === "agent" ? "直接运行 Agent" : step.text}
      </Typography.Paragraph>
      <Typography.Text type="secondary" className="text-[11px]">
        完成 {completed}/{experiment.variants.length}
      </Typography.Text>
    </div>
  );
}

function StepCell({
  step,
  variant,
  experiment,
  status,
  annotation,
  running,
  disabled,
  onVerdict,
  onNote,
  onRetry,
}: {
  step: ExperimentStep;
  variant: ExperimentVariant;
  experiment: Experiment;
  status?: VariantRunStatus;
  annotation?: ExperimentAnnotation;
  running: boolean;
  disabled?: boolean;
  onVerdict: (verdict: ExperimentAnnotation["verdict"]) => void;
  onNote: (note: string) => void;
  onRetry: () => void;
}) {
  const [note, setNote] = useState(annotation?.note ?? "");
  const session: AgentTestSession = variant;
  const turns = locateStepTurns(session, step.index, experiment);
  const verdict = annotation?.verdict ?? "";
  const busy = running || status?.status === "running" || status?.status === "queued";

  return (
    <div
      className={`flex min-w-0 flex-col gap-2 rounded border p-2 ${
        verdict === "better" ? "border-l-4 border-l-green-500 border-slate-200" : "border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-1">
        <Space size={4} wrap>
          {busy ? <Tag color="processing">运行中</Tag> : null}
          {status?.status === "cancelled" ? <Tag>已取消</Tag> : null}
          {status?.status === "failed" ? <Tag color="red">失败</Tag> : null}
          {verdict === "better" ? <Tag color="green">更好</Tag> : null}
          {verdict === "worse" ? <Tag color="default">更差</Tag> : null}
        </Space>
        <Space size={4}>
          <Button
            size="small"
            type={verdict === "better" ? "primary" : "text"}
            disabled={disabled}
            aria-label={`第 ${step.index + 1} 步 ${variant.label} 更好`}
            onClick={() => onVerdict(verdict === "better" ? "" : "better")}
          >
            更好
          </Button>
          <Button
            size="small"
            type={verdict === "worse" ? "primary" : "text"}
            disabled={disabled}
            aria-label={`第 ${step.index + 1} 步 ${variant.label} 更差`}
            onClick={() => onVerdict(verdict === "worse" ? "" : "worse")}
          >
            更差
          </Button>
        </Space>
      </div>

      {busy ? <Progress percent={100} status="active" showInfo={false} size="small" /> : null}

      {status?.status === "failed" ? (
        <div className="flex flex-col gap-2">
          <Typography.Text type="danger" className="text-xs">{status.error || "该变体运行失败"}</Typography.Text>
          <Button size="small" disabled={disabled} onClick={onRetry}>重试这一步</Button>
        </div>
      ) : turns ? (
        <div className="flex flex-col gap-2">
          {turns.user ? <UserBubble text={turns.user.content} /> : null}
          {turns.assistant ? (
            <div className="rounded bg-slate-50 p-2">
              <ToolCallTimeline message={turns.assistant} />
            </div>
          ) : (
            <Typography.Text type="secondary" className="text-xs">该步还没有回复。</Typography.Text>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-1 py-4 text-slate-400">
          <MinusCircleOutlined />
          <Typography.Text type="secondary" className="text-xs">
            {status?.status === "cancelled" ? "已取消，这一格没有产生回复。" : "未运行"}
          </Typography.Text>
        </div>
      )}

      <Input
        size="small"
        value={note}
        disabled={disabled}
        placeholder="备注（可选）"
        aria-label={`第 ${step.index + 1} 步 ${variant.label} 备注`}
        onChange={(event) => setNote(event.target.value)}
        onBlur={() => { if (note !== (annotation?.note ?? "")) onNote(note); }}
        onPressEnter={() => { if (note !== (annotation?.note ?? "")) onNote(note); }}
      />
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="ml-auto max-w-[90%] rounded-xl bg-blue-50 px-3 py-2 text-sm text-slate-700 whitespace-pre-wrap break-words">
      {text}
    </div>
  );
}
