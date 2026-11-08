"use client";

import { ArrowDownOutlined, ArrowLeftOutlined, CaretRightOutlined, EditOutlined, ForkOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Empty, Input, List, Modal, Radio, Space, Tag, Typography } from "antd";
import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { DataDirBanner } from "@/features/settings/DataDirBanner";
import { useAccount } from "@/features/account/AccountProvider";
import { SplitSessionLayout, useSplitSessionMobile } from "@/components/SplitSessionLayout";
import { SessionMessages } from "./components/SessionMessages";
import { usePlaygroundWorkbench } from "./hooks/usePlaygroundWorkbench";
import { PromptEditorField } from "./components/PromptEditorField";
import { useStickToBottom } from "@/components/useStickToBottom";

export function PlaygroundPage() {
  const { blocked, suspended } = useAccount();
  if (blocked && !suspended) return <DataDirBanner />;
  return <Suspense fallback={null}><PlaygroundWorkspace /></Suspense>;
}

function PlaygroundWorkspace() {
  const params = useSearchParams();
  const initial = params.get("session") ?? undefined;
  const { isMobile, mobileView, setMobileView } = useSplitSessionMobile();
  const playground = usePlaygroundWorkbench(initial);
  const [promptEditorOpen, setPromptEditorOpen] = useState(false);
  const [promptValue, setPromptValue] = useState("");
  const pinned = playground.activeSession?.pinned;
  const { scrollRef, showJumpButton, handleScroll, scrollToBottom } = useStickToBottom({
    sessionKey: playground.activeSessionId ?? "",
    followKey: playground.activeSession?.messages.length ?? 0,
  });
  const agentNames = useMemo(
    () => new Map(playground.agents.map((agent) => [agent.id, agent.name])),
    [playground.agents],
  );

  const list = (
    <Card title="模拟会话" className="flex h-full min-h-0 w-full flex-col" classNames={{ body: "min-h-0 flex-1 overflow-y-auto" }} loading={playground.loading}>
      {playground.sessions.length ? (
        <List
          dataSource={playground.sessions}
          rowKey="id"
          renderItem={(session) => (
            <List.Item
              className={`!cursor-pointer !px-2 ${session.id === playground.activeSessionId ? "!bg-blue-50" : ""}`}
              onClick={() => {
                if (playground.running) return;
                playground.selectSession(session.id);
                if (isMobile) setMobileView("detail");
              }}
            >
              <div className="flex min-w-0 flex-col gap-1">
                <Typography.Text strong className="truncate">{session.title}</Typography.Text>
                <Space size={4} wrap>
                  <Tag>{session.type === "fork" ? "从消息分叉" : "Playground"}</Tag>
                  <Tag color={session.draft ? "orange" : "default"}>
                    {session.draft ? "草稿快照" : "已保存版本"}
                  </Tag>
                  <Typography.Text type="secondary" className="text-[11px]">
                    {agentNames.get(session.agentId) ?? session.agentId}
                  </Typography.Text>
                </Space>
                {session.source?.conversationTitle ? (
                  <Typography.Text type="secondary" className="truncate text-[11px]">
                    来源：{session.source.conversationTitle}
                  </Typography.Text>
                ) : null}
              </div>
            </List.Item>
          )}
        />
      ) : (
        <Empty description="还没有模拟会话。在聊天页的任意消息上点击“从此开始模拟”即可创建。" />
      )}
    </Card>
  );

  const detail = (
    <div className="flex h-full min-h-0 w-full flex-col gap-3">
      {playground.error ? <Alert type="warning" showIcon title={playground.error} /> : null}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 min-[1400px]:grid-cols-[minmax(0,1fr)_300px]">
        <Card
          title={playground.activeSession?.title ?? "模拟会话"}
          extra={
            <Space>
              {isMobile ? <Button type="text" aria-label="返回列表" icon={<ArrowLeftOutlined />} onClick={() => setMobileView("list")} /> : null}
              <Button aria-label="重新生成" icon={<ReloadOutlined />} disabled={!playground.activeSession || playground.running} onClick={() => void playground.regenerate()}><span className="hidden sm:inline">重新生成</span></Button>
            </Space>
          }
          className="flex min-h-0 flex-col"
          classNames={{ body: "flex min-h-0 flex-1 flex-col gap-3 overflow-hidden" }}
        >
          {!playground.activeSession ? (
            <Empty description="选择左侧会话查看时间线" />
          ) : (
            <>
              {playground.activeSession.source ? (
                <div className="rounded border border-slate-200 bg-slate-50 p-2 text-xs text-slate-500" aria-label="冻结的历史">
                  <div className="mb-1">
                    冻结的输入来自“{playground.activeSession.source.conversationTitle}”
                    第 {(playground.activeSession.source.messageIndex ?? 0) + 1} 条消息，
                    共 {playground.activeSession.source.frozenChars ?? 0} 字，本轮及以后保持不变。
                  </div>
                  <details>
                    <summary className="cursor-pointer">查看冻结前缀</summary>
                    <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words">
                      {playground.frozenInputError ?? (playground.frozenInput || "（空）")}
                    </pre>
                  </details>
                </div>
              ) : null}
              <div className="relative flex min-h-0 flex-1 flex-col">
                <div ref={scrollRef} onScroll={handleScroll} className="app-scroll min-h-0 flex-1 overflow-y-auto bg-slate-50/60 p-3 sm:p-5">
                  <SessionMessages session={playground.activeSession} onForkFromTurn={(index) => void playground.forkHere(index)} />
                </div>
                {showJumpButton ? <Button
                  className="absolute right-4 bottom-4 shadow-md"
                  shape="circle"
                  aria-label="回到底部"
                  icon={<ArrowDownOutlined />}
                  onClick={() => scrollToBottom(true)}
                /> : null}
              </div>
            </>
          )}
        </Card>

        <Card title="当前版本" size="small" className="h-fit" extra={playground.activeSession ? <Button
          size="small"
          icon={<EditOutlined />}
          disabled={!playground.activeAgent || playground.running}
          onClick={() => {
            setPromptValue(pinned?.prompt ?? playground.activeAgent?.prompt ?? "");
            setPromptEditorOpen(true);
          }}
        >编辑提示词</Button> : null}>
          {pinned ? (
            <Descriptions column={1} size="small" items={[
              { key: "kind", label: "版本", children: pinned.kind === "draft" ? <Tag color="orange">草稿快照</Tag> : <Tag>已保存版本</Tag> },
              { key: "revision", label: "历史版本", children: pinned.revision_id ?? "当前配置" },
              { key: "level", label: "LLM 等级", children: `Level ${pinned.llm_level ?? 0}` },
              { key: "tools", label: "工具", children: (pinned.tools ?? []).length ? (pinned.tools ?? []).join("、") : "无" },
              { key: "requires", label: "必须调用工具", children: pinned.requires_tool ? "是" : "否" },
            ]} />
          ) : (
            <Typography.Text type="secondary">会话未记录版本快照。</Typography.Text>
          )}
          <div className="mt-2">
            <Link href={`/agent/regular-agents`}>前往编辑器</Link>
          </div>
        </Card>
      </div>

      {playground.activeSession ? (
        <Card size="small">
          <div className="flex flex-col gap-2">
            <Radio.Group
              value={playground.inputMode}
              size="small"
              disabled={playground.running}
              onChange={(event) => playground.setInputMode(event.target.value)}
              options={[
                { label: "以买家身份发送", value: "buyer" },
                { label: "直接运行 Agent", value: "agent" },
              ]}
            />
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 6 }}
              aria-label={playground.inputMode === "buyer" ? "买家消息" : "Agent 运行输入"}
              value={playground.draft}
              onChange={(event) => playground.setDraft(event.target.value)}
              disabled={playground.running}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === "Enter"
                  && (playground.inputMode === "agent" || playground.draft.trim())) {
                  event.preventDefault();
                  void playground.run();
                }
              }}
              placeholder={playground.inputMode === "buyer" ? "扮演买家继续对话..." : "可留空：让 Agent 直接对当前上下文行动。"}
            />
            <Space>
              <Button
                type="primary"
                icon={<CaretRightOutlined />}
                loading={playground.running}
                disabled={playground.running || (playground.inputMode === "buyer" && !playground.draft.trim())}
                onClick={() => void playground.run()}
              >
                {playground.inputMode === "buyer" ? "发送并运行" : "运行 Agent"}
              </Button>
              <Typography.Text type="secondary" className="text-xs">
                {playground.inputMode === "buyer" ? "你的消息会作为买家输入追加到冻结历史之后。Ctrl/⌘ + Enter 运行。" : "不追加买家消息，Agent 基于现有上下文行动。Ctrl/⌘ + Enter 运行。"}
              </Typography.Text>
            </Space>
          </div>
        </Card>
      ) : null}
    </div>
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <DataDirBanner compact />
      <div className="min-h-0 flex-1">
        <SplitSessionLayout list={list} detail={detail} mobileView={mobileView} />
      </div>
      <Typography.Text type="secondary" className="text-xs">
        <ForkOutlined /> 模拟只记录事件：不会写入发送队列，也不会操作客户端。
      </Typography.Text>
      <Modal
        title={`编辑提示词 · ${playground.activeAgent?.name ?? "Agent"}`}
        open={promptEditorOpen}
        width={900}
        destroyOnHidden
        onCancel={() => setPromptEditorOpen(false)}
        footer={<div className="flex flex-wrap justify-end gap-2">
          <Button onClick={() => setPromptEditorOpen(false)}>取消</Button>
          <Button
            loading={playground.running}
            disabled={!promptValue.trim()}
            onClick={() => {
              void playground.branchWithPrompt(promptValue, false).then((created) => { if (created) setPromptEditorOpen(false); });
            }}
          >应用到模拟分支</Button>
          <Button
            type="primary"
            loading={playground.running}
            disabled={!promptValue.trim()}
            onClick={() => {
              void playground.branchWithPrompt(promptValue, true).then((created) => { if (created) setPromptEditorOpen(false); });
            }}
          >保存配置并创建分支</Button>
        </div>}
      >
        <Alert className="mb-3" type="info" showIcon message="修改会创建当前会话的新分支，原会话的提示词快照不会改变。保存配置只更新提示词，模型等级和工具配置保持不变。" />
        <PromptEditorField value={promptValue} onChange={setPromptValue} placeholder="描述 Agent 的角色、任务、约束与输出要求。" />
      </Modal>
    </div>
  );
}
