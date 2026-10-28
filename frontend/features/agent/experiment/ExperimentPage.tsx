"use client";

import { ArrowLeftOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Empty, Input, List, Popconfirm, Space, Tag, Typography } from "antd";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { useAccount } from "@/features/account/AccountProvider";
import { SplitSessionLayout, useSplitSessionMobile } from "@/components/SplitSessionLayout";
import { formatMonthDay } from "@/domain/time";
import type { Experiment } from "@/types/experiment";
import { ExperimentCreateModal } from "./ExperimentCreateModal";
import { ComparisonBoard } from "./components/ComparisonBoard";
import { StepComposer } from "./components/StepComposer";
import { useExperimentWorkbench } from "./hooks/useExperimentWorkbench";

export function ExperimentPage() {
  const { blocked, suspended } = useAccount();
  if (blocked && !suspended) return <DataDirBanner />;
  return <Suspense fallback={null}><ExperimentWorkspace /></Suspense>;
}

function ExperimentWorkspace() {
  const params = useSearchParams();
  const initial = params.get("experiment") ?? undefined;
  const { isMobile, mobileView, setMobileView } = useSplitSessionMobile();
  const workbench = useExperimentWorkbench(initial);
  const [createOpen, setCreateOpen] = useState(false);
  const [renaming, setRenaming] = useState<string>();

  const list = (
    <Card
      title="对比实验"
      className="flex h-full min-h-0 w-full flex-col"
      classNames={{ body: "min-h-0 flex-1 overflow-y-auto" }}
      loading={workbench.loading}
      extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建实验</Button>}
    >
      {workbench.experiments.length ? (
        <List
          dataSource={workbench.experiments}
          rowKey="id"
          renderItem={(experiment) => (
            <List.Item
              className={`!cursor-pointer !px-2 ${experiment.id === workbench.activeId ? "!bg-blue-50" : ""}`}
              onClick={() => {
                workbench.selectExperiment(experiment.id);
                setRenaming(undefined);
                if (isMobile) setMobileView("detail");
              }}
              actions={[
                <Popconfirm
                  key="delete"
                  title="删除这个实验？"
                  description="实验里的变体会话会一并删除，无法恢复。"
                  okText="删除"
                  okButtonProps={{ danger: true }}
                  cancelText="取消"
                  onConfirm={() => void workbench.deleteExperiment(experiment.id)}
                >
                  <Button
                    size="small"
                    type="text"
                    danger
                    aria-label={`删除实验 ${experiment.name}`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    删除
                  </Button>
                </Popconfirm>,
              ]}
            >
              <div className="flex min-w-0 flex-col gap-1">
                <Typography.Text strong className="truncate">{experiment.name || "未命名实验"}</Typography.Text>
                <Space size={4} wrap>
                  <Tag>{workbench.agentNames.get(experiment.agentId) ?? experiment.agentId}</Tag>
                  <Typography.Text type="secondary" className="text-[11px]">
                    {experiment.steps.length} 步 · {experiment.variants.length} 版
                  </Typography.Text>
                </Space>
                <Typography.Text type="secondary" className="truncate text-[11px]">
                  更新于 {formatMonthDay(experiment.updatedAt)}
                </Typography.Text>
              </div>
            </List.Item>
          )}
        />
      ) : (
        <Empty description="还没有对比实验：从聊天消息分叉、从编辑器草稿、或粘贴一段提示词开始。" />
      )}
    </Card>
  );

  const experiment = workbench.experiment;
  const source = experiment?.source;

  const detail = (
    <div className="flex h-full min-h-0 w-full flex-col gap-3">
      {workbench.error ? <Alert type="warning" showIcon title={workbench.error} /> : null}
      <Card
        className="flex min-h-0 flex-col"
        classNames={{ body: "flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto" }}
        loading={workbench.detailLoading}
        title={
          experiment ? (
            <div className="flex flex-wrap items-center gap-2">
              {renaming === experiment.id ? (
                <Input
                  size="small"
                  defaultValue={experiment.name}
                  autoFocus
                  aria-label="实验名称"
                  className="max-w-xs"
                  onPressEnter={(event) => {
                    const name = event.currentTarget.value.trim();
                    setRenaming(undefined);
                    if (name && name !== experiment.name) void workbench.rename(name);
                  }}
                  onBlur={(event) => {
                    const name = event.currentTarget.value.trim();
                    setRenaming(undefined);
                    if (name && name !== experiment.name) void workbench.rename(name);
                  }}
                />
              ) : (
                <Typography.Text
                  strong
                  className="cursor-text"
                  onClick={() => setRenaming(experiment.id)}
                  title="点击重命名"
                >
                  {experiment.name || "未命名实验"}
                </Typography.Text>
              )}
              <Tag>{workbench.assignedAgentName}</Tag>
            </div>
          ) : "对比实验"
        }
        extra={
          <Space>
            {isMobile ? (
              <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => setMobileView("list")}>返回</Button>
            ) : null}
            <Button
              icon={<ReloadOutlined />}
              disabled={!experiment || workbench.runningStepIndex !== undefined}
              onClick={() => void workbench.refresh()}
            >
              刷新
            </Button>
          </Space>
        }
      >
        {!experiment ? (
          <Empty description="选择左侧实验查看对比结果" />
        ) : (
          <>
            {source ? (
              <div className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-500" aria-label="冻结的输入">
                <div className="mb-1">
                  {source.kind === "conversation"
                    ? `冻结的输入来自「${source.conversationTitle || "真实会话"}」第 ${(source.messageIndex ?? 0) + 1} 条消息`
                    : source.kind === "session"
                      ? `冻结的输入来自会话「${source.conversationTitle || source.sessionId || "模拟会话"}」`
                      : "冻结的输入是一段粘贴文本"}
                  ，共 {source.frozenChars ?? experiment.frozenPrefix.length} 字，本轮及以后保持不变。
                </div>
                <details>
                  <summary className="cursor-pointer">查看冻结前缀</summary>
                  <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words">
                    {experiment.frozenPrefix || "（空）"}
                  </pre>
                </details>
              </div>
            ) : null}

            <ComparisonBoard
              experiment={experiment}
              run={workbench.run}
              runningStepIndex={workbench.runningStepIndex}
              disabled={workbench.saving}
              onAnnotate={workbench.annotate}
              onPromote={workbench.promote}
              onRemoveVariant={workbench.removeVariant}
              onRetryStep={(stepIndex) => workbench.runStep(stepIndex, { force: true })}
            />

            <StepComposer
              experiment={experiment}
              runningStepIndex={workbench.runningStepIndex}
              cancelling={workbench.cancelling}
              disabled={workbench.saving}
              onAppend={workbench.addStep}
              onRun={workbench.runStep}
              onCancel={workbench.cancelRun}
            />
          </>
        )}
      </Card>
    </div>
  );

  return (
    <>
      <div className="flex h-full min-h-0 flex-col gap-3">
        <DataDirBanner compact />
        <div className="min-h-0 flex-1">
          <SplitSessionLayout list={list} detail={detail} mobileView={mobileView} />
        </div>
      </div>

      <ExperimentCreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(created: Experiment) => {
          workbench.adopt(created);
          if (isMobile) setMobileView("detail");
        }}
      />
    </>
  );
}
