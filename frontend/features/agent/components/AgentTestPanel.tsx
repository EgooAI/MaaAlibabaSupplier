"use client";

import { ExperimentOutlined, PlusOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, App, Button, Input, Radio, Space, Typography } from "antd";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { normalizeAgentTools } from "@/domain/agent/agentModel";
import { ExperimentCreateModal } from "@/features/agent/experiment/ExperimentCreateModal";
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
  const router = useRouter();
  const [input, setInput] = useState("");
  const [inputMode, setInputMode] = useState<"buyer" | "agent">("buyer");
  const [sessionId, setSessionId] = useState<string>();
  const [messages, setMessages] = useState<AgentTestMessage[]>([]);
  const [draftRun, setDraftRun] = useState(false);
  const [running, setRunning] = useState(false);
  const [restricted, setRestricted] = useState(false);
  const [error, setError] = useState<string>();
  const [experimentOpen, setExperimentOpen] = useState(false);

  /** The draft this run would use: the experiment compares it without running here. */
  const draft = useMemo(() => ({
    prompt: values.prompt ?? "",
    level: values.level ?? 0,
    tools: normalizeAgentTools(values.capabilities ?? []),
    requiresTool: Boolean(values.requiresTool),
  }), [values.prompt, values.level, values.capabilities, values.requiresTool]);

  async function run() {
    const content = input.trim();
    if (!agent?.id || running) return;
    if (inputMode === "buyer" && !content) return;
    setRunning(true);
    setError(undefined);
    try {
      const result = await backend.runAgentTest({
        agentId: String(agent.id),
        content,
        inputMode,
        ...(sessionId
          ? { sessionId }
          : { draft }),
      });
      setSessionId(String(result.session.id));
      setMessages(result.session.messages);
      setDraftRun(Boolean(result.session.draft));
      setRestricted(Boolean(result.restricted));
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
    setRestricted(false);
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
      {restricted ? <Alert type="warning" showIcon message="工具调用达到上限：结果可能不完整，已保留已发生的调用记录。" /> : null}
      {draftRun ? <Alert type="warning" showIcon message="该会话使用未保存的提示词快照运行，可在 Agent 会话页按“草稿试运行”标识识别。" /> : null}

      {messages.length ? (
        <div className="max-h-[26rem] overflow-auto rounded border border-slate-100 p-2">
          <SessionMessages session={{ id: sessionId ?? "", title: "草稿试运行", agentId: String(agent?.id ?? ""), createdAt: "", messages }} />
        </div>
      ) : (
        <Typography.Text type="secondary">输入一条测试消息，查看当前提示词的实际表现与每次工具调用。</Typography.Text>
      )}

      <Radio.Group
        value={inputMode}
        size="small"
        onChange={(event) => setInputMode(event.target.value)}
        options={[
          { label: "以买家身份发送", value: "buyer" },
          { label: "直接运行 Agent", value: "agent" },
        ]}
      />
      <Input.TextArea
        rows={3}
        value={input}
        disabled={!agent?.id || running}
        placeholder={inputMode === "buyer"
          ? "例如：客户询问 500 件彩盒的 MOQ 与交期，请给一条英文回复。"
          : "可留空：让 Agent 直接基于当前对话行动。"}
        onChange={(event) => setInput(event.target.value)}
      />
      <Space>
        <Button
          type="primary"
          icon={<SendOutlined />}
          loading={running}
          disabled={!agent?.id || (inputMode === "buyer" && !input.trim())}
          onClick={() => void run()}
        >
          {sessionId ? "继续试运行" : "试运行"}
        </Button>
        {agent?.id ? (
          <Button icon={<ExperimentOutlined />} disabled={running} onClick={() => setExperimentOpen(true)}>加入对比实验</Button>
        ) : null}
        {sessionId ? <Button icon={<PlusOutlined />} disabled={running} onClick={resetSession}>新会话</Button> : null}
      </Space>

      <ExperimentCreateModal
        open={experimentOpen}
        onClose={() => setExperimentOpen(false)}
        initialAgentId={agent?.id ? String(agent.id) : undefined}
        initialDraft={draft}
        source={{ kind: "text", text: "" }}
        onCreated={(experiment) => {
          setExperimentOpen(false);
          router.push(`/agent/experiments?experiment=${experiment.id}`);
        }}
      />
    </div>
  );
}
