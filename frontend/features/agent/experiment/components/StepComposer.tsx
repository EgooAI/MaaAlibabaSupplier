"use client";

import { CaretRightOutlined, StopOutlined } from "@ant-design/icons";
import { Button, Card, Input, Radio, Space, Typography } from "antd";
import { useState } from "react";
import type { Experiment } from "@/types/experiment";
import { orderedSteps, resolveComposerAction } from "../stepTurns";

type StepComposerProps = {
  experiment: Experiment;
  runningStepIndex?: number;
  cancelling?: boolean;
  disabled?: boolean;
  /** Appends the step and returns its index, so the caller can start it. */
  onAppend: (mode: "buyer" | "agent", text: string) => Promise<number | undefined>;
  onRun: (stepIndex: number, options?: { force?: boolean }) => void | Promise<unknown>;
  onCancel: () => void | Promise<unknown>;
};

export function StepComposer({
  experiment,
  runningStepIndex,
  cancelling,
  disabled,
  onAppend,
  onRun,
  onCancel,
}: StepComposerProps) {
  const [mode, setMode] = useState<"buyer" | "agent">("buyer");
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const running = runningStepIndex !== undefined;
  const action = resolveComposerAction(experiment);
  const steps = orderedSteps(experiment);
  const busy = disabled || running || submitting;
  const needsText = (action.kind === "append" || action.kind === "run") && mode === "buyer" && !draft.trim();

  const label = action.kind === "continue"
    ? "继续这一步"
    : action.kind === "append" && steps.length
      ? "运行下一步"
      : "运行这一步";

  async function submit() {
    if (busy) return;
    if (action.kind === "continue") {
      // Only the variants that did not finish this step are run: the backend
      // re-checks progress and skips the ones that already have an answer.
      void onRun(action.stepIndex);
      return;
    }
    if (action.kind === "run") {
      void onRun(action.stepIndex);
      return;
    }
    if (mode === "buyer" && !draft.trim()) return;
    setSubmitting(true);
    try {
      const index = await onAppend(mode, draft.trim());
      if (index === undefined) return;
      setDraft("");
      void onRun(index);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card size="small">
      <div className="flex flex-col gap-2">
        {action.kind === "continue" ? (
          <Typography.Text type="warning" className="text-xs">
            第 {action.stepIndex + 1} 步还没有在所有变体上完成。继续只会补跑未完成的变体，
            已有回答保持不变。
          </Typography.Text>
        ) : null}
        <Radio.Group
          value={mode}
          size="small"
          disabled={busy || action.kind !== "append"}
          onChange={(event) => setMode(event.target.value)}
          options={[
            { label: "以买家身份发送", value: "buyer" },
            { label: "直接运行 Agent", value: "agent" },
          ]}
        />
        <Input.TextArea
          rows={3}
          value={draft}
          disabled={busy || action.kind !== "append"}
          placeholder={
            action.kind === "append"
              ? mode === "buyer"
                ? "输入买家消息，所有变体都会收到同一条..."
                : "可留空：让每个变体直接对当前上下文行动。"
              : `这一步已经写好：「${steps.find((step) => step.index === action.stepIndex)?.text ?? ""}」`
          }
          aria-label="下一步的买家输入"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && running) {
              event.preventDefault();
              void onCancel();
              return;
            }
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <Space wrap>
          {running ? (
            <Button danger icon={<StopOutlined />} loading={cancelling} onClick={() => void onCancel()}>
              取消运行
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<CaretRightOutlined />}
              loading={submitting}
              disabled={busy || needsText}
              onClick={() => void submit()}
            >
              {label}
            </Button>
          )}
          <Typography.Text type="secondary" className="text-xs">
            {running
              ? "运行在下个回合边界停止；已完成的变体保留结果。"
              : "Ctrl/⌘ + Enter 运行，Esc 取消。"}
          </Typography.Text>
        </Space>
      </div>
    </Card>
  );
}
