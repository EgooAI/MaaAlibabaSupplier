"use client";

import { PlusOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, App, Button, Input, Space, Typography } from "antd";
import { useState } from "react";
import { normalizeAgentTools } from "@/domain/agent/agentModel";
import { backend } from "@/services/client";
import type { AgentConfig, AgentEditValues, AgentTestMessage } from "@/types/agent";
import { SessionMessages } from "./SessionMessages";

type AgentTestPanelProps = {
  agent?: AgentConfig;
  values: AgentEditValues;
};

/**
 * Draft test run: executes the current (possibly unsaved) editor values in a
 * dedicated session. A session keeps its draft prompt snapshot, so continuing
 * it later still reflects what actually ran.
 */
export function AgentTestPanel({ agent, values }: AgentTestPanelProps) {
  const { message } = App.useApp();
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string>();
  const [messages, setMessages] = useState<AgentTestMessage[]>([]);
  const [draftRun, setDraftRun] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();

  async function run() {
    const content = input.trim();
    if (!agent?.id || !content || running) return;
    setRunning(true);
    setError(undefined);
    try {
      const result = await backend.runAgentTest({
        agentId: String(agent.id),
        content,
        ...(sessionId
          ? { sessionId }
          : {
              draft: {
                prompt: values.prompt ?? "",
                level: values.level ?? 0,
                tools: normalizeAgentTools(values.capabilities ?? []),
              },
            }),
      });
      setSessionId(String(result.session.id));
      setMessages(result.session.messages);
      setDraftRun(Boolean(result.session.draft));
      setInput("");
    } catch (caught: unknown) {
      const text = caught instanceof Error ? caught.message : "试运行失败";
      setError(text);
      message.error(text);
    } finally {
      setRunning(false);
    }
  }

  function resetSession() {
    setSessionId(undefined);
    setMessages([]);
    setDraftRun(false);
    setError(undefined);
  }

  return (
    <div className="flex flex-col gap-3">
      {!agent?.id ? (
        <Alert type="info" showIcon message="Agent 尚未保存：保存后即可用当前草稿提示词试运行。" />
      ) : (
        <Alert
          type="info"
          showIcon
          message={sessionId
            ? "继续当前试运行会话：会话按创建时的草稿快照运行，不受后续编辑影响。"
            : "试运行使用当前编辑器中的未保存内容（提示词、等级与能力），不会修改已保存配置。"}
        />
      )}
      {error ? <Alert type="warning" showIcon message={error} /> : null}
      {draftRun ? <Alert type="warning" showIcon message="该会话使用未保存的提示词快照运行，可在 Agent 会话页按“草稿试运行”标识识别。" /> : null}

      {messages.length ? (
        <div className="max-h-[26rem] overflow-auto rounded border border-slate-100 p-2">
          <SessionMessages session={{ id: sessionId ?? "", title: "草稿试运行", agentId: String(agent?.id ?? ""), createdAt: "", messages }} />
        </div>
      ) : (
        <Typography.Text type="secondary">输入一条测试消息，查看当前提示词的实际表现。</Typography.Text>
      )}

      <Input.TextArea
        rows={3}
        value={input}
        disabled={!agent?.id || running}
        placeholder="例如：客户询问 500 件彩盒的 MOQ 与交期，请给一条英文回复。"
        onChange={(event) => setInput(event.target.value)}
      />
      <Space>
        <Button type="primary" icon={<SendOutlined />} loading={running} disabled={!agent?.id || !input.trim()} onClick={() => void run()}>
          {sessionId ? "继续试运行" : "试运行"}
        </Button>
        {sessionId ? <Button icon={<PlusOutlined />} disabled={running} onClick={resetSession}>新会话</Button> : null}
      </Space>
    </div>
  );
}
