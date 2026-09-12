"use client";

import { App } from "antd";
import { createContext, createElement, useContext, useEffect, useState, type ReactNode } from "react";
import { agentConfigToPreset, agentPresetToConfig, agentPresetToDbPreset, createRegularAgentPreset, dbPresetToAgentPreset, documentToLlmLevelConfig, llmLevelToDocumentConfig, upsertLlmLevel } from "@/domain/agent/agentModel";
import { nowText } from "@/domain/time";
import { backend } from "@/services/client";
import type { AgentConfig, AgentConsoleState, AgentEditValues, DbAgentPreset, LlmLevelConfig } from "@/types/agent";

function useAgentWorkbenchController() {
  const { message } = App.useApp();
  const [state, setState] = useState<AgentConsoleState>();
  const [loading, setLoading] = useState(true);
  const [agentMutationId, setAgentMutationId] = useState<string>();

  useEffect(() => {
    let mounted = true;
    backend.getAgentConsole()
      .then((data) => {
        if (mounted) setState(data);
      })
      .catch((error: unknown) => {
        if (mounted) message.error(error instanceof Error ? error.message : "Agent 控制台加载失败");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [message]);

  async function saveLlmLevel(config: LlmLevelConfig) {
    try {
      const saved = await backend.saveLlmConfig(llmLevelToDocumentConfig(config));
      setState((currentState) => {
        if (!currentState) return currentState;
        const savedLevel = documentToLlmLevelConfig(saved);
        const nextLevels = upsertLlmLevel(currentState.llmLevels ?? [], savedLevel);
        return { ...currentState, llmLevels: nextLevels };
      });
      message.success("LLM 参数已保存");
      return true;
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "LLM 参数保存失败");
      return false;
    }
  }

  async function toggleAgent(agent: AgentConfig, enabled: boolean) {
    if (agentMutationId) return false;
    setAgentMutationId(agent.id);
    try {
      const updatedAt = nowText();
      const preset = agentConfigToPreset({ ...agent, enabled }, {
        name: agent.name,
        description: agent.description,
        prompt: agent.prompt ?? "",
        level: agent.level ?? 0,
        capabilities: agent.capabilities,
      }, updatedAt);
      const saved = await backend.saveAgentPreset(agentPresetToDbPreset(preset));
      syncAgentState(saved);
      message.success(enabled ? "Agent 已启用" : "Agent 已停用");
      return true;
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "Agent 状态保存失败");
      return false;
    } finally {
      setAgentMutationId(undefined);
    }
  }

  async function saveAgent(agent: AgentConfig, values: AgentEditValues) {
    if (agentMutationId) return false;
    setAgentMutationId(agent.id);
    try {
      const preset = agentConfigToPreset(agent, values, nowText());
      const saved = await backend.saveAgentPreset(agentPresetToDbPreset(preset));
      syncAgentState(saved);
      message.success("Agent 已保存");
      return true;
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "Agent 保存失败");
      return false;
    } finally {
      setAgentMutationId(undefined);
    }
  }

  async function createAgent(values: AgentEditValues) {
    if (agentMutationId) return false;
    setAgentMutationId("new-agent");
    try {
      const preset = createRegularAgentPreset(values, nowText());
      const saved = await backend.saveAgentPreset(agentPresetToDbPreset(preset));
      syncAgentState(saved);
      message.success("Agent 已创建");
      return true;
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "Agent 创建失败");
      return false;
    } finally {
      setAgentMutationId(undefined);
    }
  }

  async function deleteAgent(agent: AgentConfig) {
    if (agent.category !== "regular" || agentMutationId) return false;
    setAgentMutationId(agent.id);
    try {
      const deleted = await backend.deleteAgentPreset(agent.id);
      if (!deleted) {
        message.error("Agent 未删除");
        return false;
      }
      setState((current) => current ? {
        ...current,
        agents: current.agents.filter((item) => item.id !== agent.id),
        agentPresets: current.agentPresets?.filter((item) => item.id !== agent.id),
      } : current);
      message.success("Agent 已删除");
      return true;
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "Agent 删除失败");
      return false;
    } finally {
      setAgentMutationId(undefined);
    }
  }

  async function resetSystemAgent(agent: AgentConfig) {
    if (agent.category !== "system" || !agent.apid || agentMutationId) return false;
    setAgentMutationId(agent.id);
    try {
      const restored = await backend.restoreSystemAgentDefault(agent.apid);
      syncAgentState(restored);
      message.success("系统 Agent 已重置");
      return true;
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : "系统 Agent 重置失败");
      return false;
    } finally {
      setAgentMutationId(undefined);
    }
  }

  function syncAgentState(dbPreset: DbAgentPreset) {
    setState((current) => {
      if (!current) return current;
      const currentPreset = current.agentPresets?.find((item) => String(item.id) === dbPreset.apid);
      const preset = dbPresetToAgentPreset(dbPreset, currentPreset);
      const updated = agentPresetToConfig(preset);
      const agentId = String(updated.id);
      const hasAgent = current.agents.some((item) => String(item.id) === agentId);
      const hasPreset = current.agentPresets?.some((item) => String(item.id) === agentId) ?? false;
      return {
        ...current,
        agents: hasAgent ? current.agents.map((item) => String(item.id) === agentId ? updated : item) : [updated, ...current.agents],
        agentPresets: current.agentPresets
          ? (hasPreset ? current.agentPresets.map((item) => String(item.id) === agentId ? preset : item) : [preset, ...current.agentPresets])
          : [preset],
      };
    });
  }

  return { state, loading, saveLlmLevel, toggleAgent, saveAgent, createAgent, deleteAgent, resetSystemAgent, agentMutationId };
}

type AgentWorkbench = ReturnType<typeof useAgentWorkbenchController>;
const AgentWorkbenchContext = createContext<AgentWorkbench | null>(null);

export function AgentWorkbenchProvider({ children }: { children: ReactNode }) {
  const workbench = useAgentWorkbenchController();

  return createElement(AgentWorkbenchContext.Provider, { value: workbench }, children);
}

export function useAgentWorkbench() {
  const workbench = useContext(AgentWorkbenchContext);
  if (!workbench) {
    throw new Error("useAgentWorkbench must be used within AgentWorkbenchProvider");
  }
  return workbench;
}
