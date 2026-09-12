"use client";

import { ArrowDownOutlined, ArrowLeftOutlined, CopyOutlined, DeleteOutlined, MoreOutlined, PlusOutlined, ReloadOutlined, RobotOutlined, UserOutlined } from "@ant-design/icons";
import { Avatar, Button, Card, Col, Dropdown, Empty, Form, Grid, Listy, Modal, Row, Select, Space, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { useState } from "react";
import { ActionConfirmModal } from "@/components/ActionConfirmModal";
import { SessionListPanel } from "@/components/SessionListPanel";
import { MessageComposer } from "@/components/MessageComposer";
import { useStickToBottom } from "@/components/useStickToBottom";
import { canRunAgentExecution, formatAgentSessionDate } from "@/domain/agent/agentModel";
import { fallbackAvatarUrl, sellerAvatarUrl } from "@/domain/chat/avatarModel";
import type { AgentTestSession } from "@/types/agent";
import { useAgentSessionWorkbench } from "./hooks/useAgentSessionWorkbench";

type CreateSessionValues = {
  agentId: string;
};
type MobileSessionView = "list" | "detail";

export function AgentSessionsPage() {
  const screens = Grid.useBreakpoint();
  const workbench = useAgentSessionWorkbench();
  const [createOpen, setCreateOpen] = useState(false);
  const [mobileView, setMobileView] = useState<MobileSessionView>("list");
  const isMobile = screens.xl === false;
  const [pendingDeleteSession, setPendingDeleteSession] = useState<AgentTestSession>();
  const [form] = Form.useForm<CreateSessionValues>();
  const agentNames = new Map(workbench.agents.map((agent) => [agent.id, agent.name]));
  const canRunActiveAgent = canRunAgentExecution(workbench.activeAgent);
  const sessionActionBusy = Boolean(workbench.action);
  const timeline = useStickToBottom({
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
        <Listy
          items={workbench.sessions}
          rowKey="id"
          virtual={false}
          itemRender={(session) => (
            <SessionListItem
              session={session}
              agentName={agentNames.get(session.agentId) ?? session.agentId}
              active={session.id === workbench.activeSessionId}
              deleting={workbench.action?.type === "delete" && workbench.action.sessionId === session.id}
              disabled={sessionActionBusy}
              onClick={() => handleSelectSession(session.id)}
              onCopy={() => workbench.copySession(session.id)}
              onDelete={() => setPendingDeleteSession(session)}
            />
          )}
        />
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
        </Space>
      }
      className="flex h-full min-h-0 w-full flex-col"
      classNames={{ body: "flex min-h-0 flex-1 flex-col" }}
    >
      {workbench.activeSession ? (
        <div className="flex min-h-0 flex-1 flex-col gap-6">
          <div className="relative flex min-h-0 flex-1 flex-col">
            <div ref={timeline.scrollRef} onScroll={timeline.handleScroll} className="min-h-0 flex-1 overflow-y-auto pr-2">
              <SessionMessages session={workbench.activeSession} />
            </div>
            {timeline.showJumpButton ? (
              <Button
                size="small"
                shape="round"
                icon={<ArrowDownOutlined />}
                className="absolute bottom-2 left-1/2 -translate-x-1/2 shadow"
                onClick={() => timeline.scrollToBottom(true)}
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
    <div className="flex h-[calc(100vh-7rem)] min-h-[480px] flex-col">
      <Row gutter={[16, 16]} className="min-h-0 flex-1">
        {isMobile ? (
          <Col xs={24} className="flex h-full min-h-0">{mobileView === "list" ? sessionList : sessionDetail}</Col>
        ) : (
          <>
            <Col xs={24} xl={6} className="flex h-full min-h-0">{sessionList}</Col>
            <Col xs={24} xl={18} className="flex h-full min-h-0">{sessionDetail}</Col>
          </>
        )}
      </Row>

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
    </div>
  );
}

type SessionListItemProps = {
  session: AgentTestSession;
  agentName: string;
  active: boolean;
  disabled: boolean;
  deleting: boolean;
  onClick: () => void;
  onCopy: () => void;
  onDelete: () => void;
};

function SessionListItem({ session, agentName, active, disabled, deleting, onClick, onCopy, onDelete }: SessionListItemProps) {
  const menuItems: MenuProps["items"] = [
    { key: "copy", icon: <CopyOutlined />, label: "复制", disabled: deleting },
    { key: "delete", icon: <DeleteOutlined />, label: "删除", danger: true, disabled: deleting },
  ];

  function handleMenuClick(info: Parameters<NonNullable<MenuProps["onClick"]>>[0]) {
    info.domEvent.stopPropagation();
    if (info.key === "copy") void onCopy();
    if (info.key === "delete") onDelete();
  }

  return (
    <div className={`rounded-lg px-3 py-3 ${disabled ? "cursor-default" : "cursor-pointer"} ${active ? "bg-blue-50" : "hover:bg-slate-50"}`} onClick={onClick}>
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <Typography.Text strong ellipsis>{session.title}</Typography.Text>
            <Typography.Text className="shrink-0 text-xs">{formatAgentSessionDate(session.createdAt)}</Typography.Text>
          </div>
          <div className="mt-1">
            <Tag color="blue">{agentName}</Tag>
          </div>
        </div>
        <Dropdown menu={{ items: menuItems, onClick: handleMenuClick }} trigger={["click"]} disabled={disabled}>
          <Button
            type="text"
            size="small"
            icon={deleting ? <ReloadOutlined spin /> : <MoreOutlined />}
            aria-label={`操作会话：${session.title}`}
            onClick={(event) => event.stopPropagation()}
          />
        </Dropdown>
      </div>
    </div>
  );
}

function SessionMessages({ session }: { session: AgentTestSession }) {
  const [userAvatar, setUserAvatar] = useState(sellerAvatarUrl);

  return (
    <Space orientation="vertical" size="middle" className="w-full">
      {session.messages.map((item) => {
        const isAssistant = item.role === "assistant";
        return (
          <div key={item.id} className={`flex ${isAssistant ? "justify-start" : "justify-end"}`}>
            <div className={`flex max-w-[78%] gap-3 ${isAssistant ? "" : "flex-row-reverse"}`}>
              <Avatar
                className="shrink-0"
                src={isAssistant ? undefined : userAvatar}
                icon={isAssistant ? <RobotOutlined /> : <UserOutlined />}
                style={{ backgroundColor: isAssistant ? "#64748b" : "#e2e8f0" }}
                onError={isAssistant ? undefined : () => {
                  setUserAvatar(fallbackAvatarUrl);
                  return true;
                }}
              />
              <Card size="small" className={isAssistant ? "bg-slate-50" : "bg-blue-50"}>
                <Typography.Text className="text-xs">{item.createdAt}</Typography.Text>
                <Typography.Paragraph className="!mb-0 mt-2 whitespace-pre-wrap">{item.content}</Typography.Paragraph>
              </Card>
            </div>
          </div>
        );
      })}
    </Space>
  );
}
