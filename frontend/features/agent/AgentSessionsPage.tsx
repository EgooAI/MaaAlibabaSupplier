"use client";

import { ArrowDownOutlined, ArrowLeftOutlined, PlusOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Empty, Form, Modal, Select, Space } from "antd";
import { useState } from "react";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { SessionListPanel } from "@/components/SessionListPanel";
import { MessageComposer } from "@/components/MessageComposer";
import { SplitSessionLayout, useSplitSessionMobile } from "@/components/SplitSessionLayout";
import { useStickToBottom } from "@/components/useStickToBottom";
import { canRunAgentExecution, formatAgentSessionDate } from "@/domain/agent/agentModel";
import type { AgentTestSession } from "@/types/agent";
import { SessionListItem } from "./components/SessionListItem";
import { SessionMessages } from "./components/SessionMessages";
import { useAgentSessionWorkbench } from "./hooks/useAgentSessionWorkbench";

type CreateSessionValues = {
  agentId: string;
};

export function AgentSessionsPage() {
  const { isMobile, mobileView, setMobileView } = useSplitSessionMobile();
  const workbench = useAgentSessionWorkbench();
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingDeleteSession, setPendingDeleteSession] = useState<AgentTestSession>();
  const [form] = Form.useForm<CreateSessionValues>();
  const agentNames = new Map(workbench.agents.map((agent) => [agent.id, agent.name]));
  const canRunActiveAgent = canRunAgentExecution(workbench.activeAgent);
  const sessionActionBusy = Boolean(workbench.action);
  const { scrollRef, showJumpButton, handleScroll, scrollToBottom } = useStickToBottom({
    sessionKey: workbench.activeSessionId ?? "",
    followKey: workbench.activeSession?.messages.length ?? 0,
  });

  function handleSelectSession(id: string) {
    workbench.selectSession(id);
    if (isMobile) setMobileView("detail");
  }
  async function handleCreate(values: CreateSessionValues) {
    const created = await workbench.createSession(values.agentId);
    if (!created) return;
    form.resetFields();
    setCreateOpen(false);
  }

  async function handleDelete() {
    if (!pendingDeleteSession) return;
    const deleted = await workbench.deleteSession(pendingDeleteSession.id);
    if (deleted) setPendingDeleteSession(undefined);
  }

  const sessionList = (
    <SessionListPanel title="会话列表" loading={workbench.loading}>
      {workbench.sessions.length ? (
        <div className="flex flex-col gap-1">
          {workbench.sessions.map((session) => (
            <SessionListItem
              key={session.id}
              session={session}
              agentName={agentNames.get(session.agentId) ?? session.agentId}
              active={session.id === workbench.activeSessionId}
              deleting={workbench.action?.type === "delete" && workbench.action.sessionId === session.id}
              disabled={sessionActionBusy}
              onClick={() => handleSelectSession(session.id)}
              onCopy={() => workbench.copySession(session.id)}
              onDelete={() => setPendingDeleteSession(session)}
            />
          ))}
        </div>
      ) : (
        <Empty description="暂无 Agent 会话" />
      )}
    </SessionListPanel>
  );
  const sessionDetail = (
    <Card
      title={workbench.activeSession ? `${agentNames.get(workbench.activeSession.agentId) ?? workbench.activeSession.agentId} · ${workbench.activeSession.title}` : "会话详情"}
      extra={
        <Space>
          {isMobile ? <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => setMobileView("list")}>返回列表</Button> : null}
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)} disabled={workbench.busy}>新建会话</Button>
          <Button loading={workbench.reconciling} disabled={workbench.busy} onClick={() => void workbench.reconcileHistory()}>刷新历史</Button>
        </Space>
      }
      className="flex h-full min-h-0 w-full flex-col"
      classNames={{ body: "flex min-h-0 flex-1 flex-col" }}
    >
      {workbench.operationError ? <Alert type="warning" showIcon title={workbench.operationError} /> : null}
      {workbench.activeSession ? (
        <div className="flex min-h-0 flex-1 flex-col gap-6">
          <div className="relative flex min-h-0 flex-1 flex-col">
            <div ref={scrollRef} onScroll={handleScroll} className="min-h-0 flex-1 overflow-y-auto pr-2">
              <SessionMessages session={workbench.activeSession} />
            </div>
            {showJumpButton ? (
              <Button
                size="small"
                shape="round"
                icon={<ArrowDownOutlined />}
                className="absolute bottom-2 left-1/2 -translate-x-1/2 shadow"
                onClick={() => scrollToBottom(true)}
              >
                回到底部
              </Button>
            ) : null}
          </div>
          <div className="shrink-0">
            <MessageComposer
              value={workbench.draft}
              onChange={workbench.setDraft}
              tools={[
                { key: "undo-turn", label: "撤销一轮", disabled: workbench.busy || !workbench.canUndoTurn },
                { key: "regenerate-reply", label: "重新回复", disabled: workbench.busy || !canRunActiveAgent || !workbench.canRegenerateReply },
              ]}
              onToolClick={(key) => {
                if (key === "undo-turn") void workbench.undoTurn();
                if (key === "regenerate-reply") void workbench.regenerateReply();
              }}
              placeholder="输入要交给 Agent 处理的问题或任务..."
              loading={workbench.action?.type === "send"}
              disabled={!canRunActiveAgent}
              onSend={workbench.sendMessage}
            />
          </div>
        </div>
      ) : (
        <Empty description="请选择会话或新建会话" />
      )}
    </Card>
  );

  return (
    <>
      <SplitSessionLayout list={sessionList} detail={sessionDetail} mobileView={mobileView} />

      <Modal
        title="新建 Agent 会话"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item name="agentId" label="普通 Agent" rules={[{ required: true, message: "请选择普通 Agent" }]}>
            <Select placeholder="选择要对话的普通 Agent" options={workbench.agents.map((agent) => ({ label: agent.name, value: agent.id, disabled: !agent.enabled }))} />
          </Form.Item>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setCreateOpen(false)}>取消</Button>
            <Button type="primary" htmlType="submit" loading={workbench.creating}>创建会话</Button>
          </div>
        </Form>
      </Modal>

      <ActionConfirmModal
        title="删除 Agent 会话"
        open={Boolean(pendingDeleteSession)}
        warning="确认删除该会话？删除后无法恢复。"
        okText="删除"
        loading={workbench.action?.type === "delete" && workbench.action.sessionId === pendingDeleteSession?.id}
        onCancel={() => setPendingDeleteSession(undefined)}
        onConfirm={handleDelete}
        details={pendingDeleteSession ? [
          { label: "会话标题", value: pendingDeleteSession.title },
          { label: "Agent", value: agentNames.get(pendingDeleteSession.agentId) ?? pendingDeleteSession.agentId },
          { label: "创建时间", value: formatAgentSessionDate(pendingDeleteSession.createdAt) },
          { label: "消息数", value: pendingDeleteSession.messages.length },
        ] : undefined}
      />
    </>
  );
}
