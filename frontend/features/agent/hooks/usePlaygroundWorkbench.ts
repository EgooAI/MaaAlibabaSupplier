"use client";

import { App } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { backend } from "@/services/client";
import { operationErrorMessage } from "@/services/errors";
import type { AgentConfig, AgentTestSession } from "@/types/agent";

/** Sessions the Playground owns: forked/simulated ones only. */
export function isPlaygroundSession(session: AgentTestSession) {
  // Experiment variants are playground sessions too, but their transcript is
  // owned by the comparison board; continuing them here would desync the run.
  if (session.experimentId) return false;
  return session.type === "fork" || session.type === "playground";
}

export function usePlaygroundWorkbench(initialSessionId?: string) {
  const { message } = App.useApp();
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [sessions, setSessions] = useState<AgentTestSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | undefined>(initialSessionId);
  const [activeSession, setActiveSession] = useState<AgentTestSession>();
  const [frozenInput, setFrozenInput] = useState("");
  const [frozenInputError, setFrozenInputError] = useState<string>();
  const [draft, setDraft] = useState("");
  const [inputMode, setInputMode] = useState<"buyer" | "agent">("buyer");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();
  const selection = useRef(0);

  useEffect(() => {
    let mounted = true;
    backend.getAgentConsole()
      .then(async (state) => {
        if (!mounted) return;
        setAgents(state.agents ?? []);
        const history = state.history ?? await backend.listAgentTestHistory();
        const playground = history.filter(isPlaygroundSession);
        setSessions(playground);
        setActiveSessionId((current) => current && playground.some((item) => item.id === current)
          ? current
          : playground[0]?.id);
      })
      .catch((caught: unknown) => {
        if (mounted) setError(caught instanceof Error ? caught.message : "模拟会话加载失败");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    let cancelled = false;
    const selected = selection.current;
    backend.getAgentSession(activeSessionId)
      .then(({ session, frozenInput: frozen }) => {
        if (cancelled || selected !== selection.current) return;
        if (!isPlaygroundSession(session)) {
          throw new Error("该会话不是模拟会话，无法在 Playground 中打开");
        }
        setActiveSession(session);
        setFrozenInput(frozen);
        setFrozenInputError(undefined);
        setDraft("");
      })
      .catch((caught: unknown) => {
        if (cancelled || selected !== selection.current) return;
        setActiveSession(undefined);
        setFrozenInput("");
        setError(caught instanceof Error ? caught.message : "会话读取失败");
      });
    return () => { cancelled = true; };
  }, [activeSessionId]);

  const activeAgent = useMemo(
    () => agents.find((agent) => agent.id === activeSession?.agentId),
    [agents, activeSession?.agentId],
  );

  function replaceSession(session: AgentTestSession) {
    setActiveSession(session);
    setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
  }

  async function run() {
    if (!activeSession || running) return;
    const selected = selection.current;
    const content = draft.trim();
    if (inputMode === "buyer" && !content) return;
    setRunning(true);
    setError(undefined);
    try {
      const result = await backend.runAgentTest({
        agentId: activeSession.agentId,
        content,
        sessionId: activeSession.id,
        inputMode,
      });
      if (selected !== selection.current) return;
      replaceSession(result.session);
      setDraft("");
    } catch (caught: unknown) {
      setError(operationErrorMessage(caught, "模拟运行失败"));
    } finally {
      setRunning(false);
    }
  }

  async function regenerate() {
    if (!activeSession || running) return;
    const selected = selection.current;
    setRunning(true);
    setError(undefined);
    try {
      const result = await backend.regenerateAgentTestSessionReply(activeSession.id);
      if (selected !== selection.current) return;
      replaceSession(result);
    } catch (caught: unknown) {
      setError(operationErrorMessage(caught, "重新生成失败"));
    } finally {
      setRunning(false);
    }
  }

  async function forkHere(index: number) {
    if (!activeSession || running) return;
    const selected = selection.current;
    setRunning(true);
    try {
      const copy = await backend.branchAgentTestSession(activeSession.id, index);
      if (selected !== selection.current) return;
      setSessions((current) => [copy, ...current.filter((item) => item.id !== copy.id)]);
      setActiveSessionId(copy.id);
      message.success("已从该回合分叉");
    } catch (caught: unknown) {
      setError(operationErrorMessage(caught, "分叉失败"));
    } finally {
      setRunning(false);
    }
  }

  async function removeSession(id: string) {
    if (running) return;
    try {
      await backend.deleteAgentTestSession(id);
      setSessions((current) => current.filter((item) => item.id !== id));
      if (id === activeSessionId) {
        const next = sessions.find((item) => item.id !== id);
        setActiveSessionId(next?.id);
        setActiveSession(undefined);
      }
    } catch (caught: unknown) {
      setError(operationErrorMessage(caught, "删除失败"));
    }
  }

  return {
    agents, sessions, activeSession, activeSessionId, activeAgent,
    frozenInput, frozenInputError, draft, setDraft, inputMode, setInputMode,
    loading, running, error, run, regenerate, forkHere, removeSession,
    selectSession: (id: string) => { ++selection.current; setActiveSessionId(id); },
  };
}
