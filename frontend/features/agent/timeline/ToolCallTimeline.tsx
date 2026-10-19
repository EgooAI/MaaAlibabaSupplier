"use client";

import { Alert } from "antd";
import { Typography } from "antd";
import { renderContentHtml } from "@/domain/render/markdown";
import type { AgentTestMessage } from "@/types/agent";
import { ToolCallCard } from "./ToolCallCard";
import "./defaultRenderers";

/**
 * One agent turn: its tool calls in execution order, then the final text.
 *
 * An empty final text is normal for tool-driven agents, so the turn renders as
 * cards only. Footer numbers are shown only when the run actually reported them.
 */
export function ToolCallTimeline({ message }: { message: AgentTestMessage }) {
  const calls = message.toolCalls ?? [];
  const simulated = message.mode === "simulated";
  const text = (message.content ?? "").trim();
  if (!calls.length && !text) return null;

  return (
    <div className="flex flex-col gap-2">
      {message.restricted ? (
        <Alert type="warning" showIcon title="工具调用达到上限，结果可能不完整。" />
      ) : null}
      {calls.length ? (
        <div className="flex flex-col gap-1.5">
          {calls.map((call, index) => (
            <ToolCallCard key={call.callId ?? `${message.id}-tool-${index}`} call={call} simulated={simulated} />
          ))}
        </div>
      ) : null}
      {text ? (
        <div className="whitespace-pre-wrap break-words text-sm" dangerouslySetInnerHTML={renderContentHtml(text, "markdown")} />
      ) : null}
      <TurnFooter message={message} roundsLabel="LLM 轮次" />
    </div>
  );
}

export function TurnFooter({ message }: { message: AgentTestMessage; roundsLabel?: string }) {
  const parts: string[] = [];
  if (message.iterations) parts.push(`LLM ${message.iterations} 轮`);
  if (typeof message.durationMs === "number" && message.durationMs > 0) {
    parts.push(`${(message.durationMs / 1000).toFixed(1)}s`);
  }
  const usage = message.usage;
  const tokens = usage?.totalTokens ?? null;
  if (tokens !== null && tokens !== undefined) parts.push(`${tokens} tokens`);
  else if (usage?.promptTokens || usage?.completionTokens) {
    parts.push(`${(usage.promptTokens ?? 0) + (usage.completionTokens ?? 0)} tokens`);
  }
  if (!parts.length) return null;
  return (
    <Typography.Text type="secondary" className="text-[11px]">
      {parts.join(" · ")}
    </Typography.Text>
  );
}
