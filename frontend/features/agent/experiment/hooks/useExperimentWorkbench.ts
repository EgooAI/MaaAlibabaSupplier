"use client";

import { App } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { backend } from "@/services/client";
import { operationErrorMessage } from "@/services/errors";
import { ExperimentRunConflict } from "@/services/httpAdapter";
import type { AgentConfig } from "@/types/agent";
import type {
  Experiment,
  ExperimentAnnotation,
  ExperimentSummary,
  ExperimentVariantSummary,
  RunSnapshot,
} from "@/types/experiment";
import { isRunFinished } from "@/types/experiment";
import { orderedSteps } from "../stepTurns";

/** How often an unfinished run is re-read: the backend has no push channel. */
const RUN_POLL_MS = 800;

/** A summary row from the full experiment, so the list reflects the edit without a refetch. */
function summarize(experiment: Experiment): ExperimentSummary {
  const variants: ExperimentVariantSummary[] = experiment.variants.map((variant) => ({
    id: variant.id,
    label: variant.label,
    turnCount: variant.messages.length,
    draft: Boolean(variant.draft) || variant.pinned?.kind === "draft",
    pinned: variant.pinned ?? null,
  }));
  return { ...experiment, variants };
}

/**
 * Detail, run and failure are stored with the experiment they belong to. A
 * switch therefore never needs to clear them: the getters below ignore anything
 * that does not match the current selection, which also keeps a late response
 * from the previous experiment from leaking into the new one.
 */
type Loaded = { id: string; experiment?: Experiment };
type RunState = { experimentId: string; run?: RunSnapshot; stepIndex?: number; cancelling: boolean };
type Failure = { id: string | null; text: string };

export function useExperimentWorkbench(initialId?: string) {
  const { message } = App.useApp();
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [activeId, setActiveId] = useState<string | undefined>(initialId);
  const [loaded, setLoaded] = useState<Loaded>();
  const [runState, setRunState] = useState<RunState>();
  const [failure, setFailure] = useState<Failure>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // Every async continuation captures this counter and drops its result once a
  // newer selection landed, so a slow response cannot overwrite the workspace.
  const selection = useRef(0);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const experiment = loaded && loaded.id === activeId ? loaded.experiment : undefined;
  const detailLoading = Boolean(activeId) && loaded?.id !== activeId;
  const error = failure && (failure.id === null || failure.id === activeId) ? failure.text : undefined;
  // Narrowed once: the getters below read the same record.
  const activeRun = runState && runState.experimentId === activeId ? runState : undefined;
  const run = activeRun?.run;
  const runningStepIndex = activeRun?.stepIndex;
  const cancelling = activeRun?.cancelling ?? false;

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== undefined) {
      clearTimeout(pollTimer.current);
      pollTimer.current = undefined;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  useEffect(() => {
    let mounted = true;
    backend.listExperiments()
      .then((rows) => {
        if (!mounted) return;
        setExperiments(rows);
        setActiveId((current) => current && rows.some((row) => row.id === current) ? current : rows[0]?.id);
      })
      .catch((caught: unknown) => {
        if (mounted) setFailure({ id: null, text: operationErrorMessage(caught, "实验列表加载失败") });
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    // Agent names decorate the list; a failure here must not block the page.
    backend.getAgentConsole()
      .then((state) => { if (mounted) setAgents(state.agents ?? []); })
      .catch(() => {});
    return () => { mounted = false; };
  }, []);

  /** Re-reads one experiment and folds it back into both the detail and the list row. */
  const reload = useCallback(async (id: string, selected: number) => {
    const detail = await backend.getExperiment(id);
    if (selected !== selection.current) return undefined;
    setLoaded({ id, experiment: detail });
    setExperiments((current) => upsert(current, summarize(detail)));
    return detail;
  }, []);

  /**
   * Reads the run until it reaches a terminal state, then refreshes the variant
   * transcripts. A conflict on start attaches here too, so a second click
   * watches the run already going instead of reporting a failure.
   */
  const pollRun = useCallback((runId: string, id: string, selected: number) => {
    stopPolling();
    const tick = async () => {
      try {
        const snapshot = await backend.getExperimentRun(runId);
        if (selected !== selection.current) return;
        const finished = isRunFinished(snapshot.status);
        setRunState({ experimentId: id, run: snapshot, stepIndex: finished ? undefined : snapshot.stepIndex, cancelling: false });
        if (!finished) {
          pollTimer.current = setTimeout(() => void tick(), RUN_POLL_MS);
          return;
        }
        stopPolling();
        // Variants' messages only exist after the run wrote them: refetch the detail.
        await reload(id, selected);
      } catch (caught: unknown) {
        if (selected !== selection.current) return;
        stopPolling();
        setRunState(undefined);
        setFailure({ id, text: operationErrorMessage(caught, "运行状态读取失败") });
      }
    };
    void tick();
  }, [reload, stopPolling]);

  useEffect(() => {
    if (!activeId) return;
    const selected = ++selection.current;
    stopPolling();
    backend.getExperiment(activeId)
      .then((detail) => {
        if (selected !== selection.current) return;
        setLoaded({ id: activeId, experiment: detail });
        // A run survives a page reload: reattach to it instead of showing a
        // stale "not running" board.
        if (detail.activeRunId) pollRun(detail.activeRunId, detail.id, selected);
      })
      .catch((caught: unknown) => {
        if (selected !== selection.current) return;
        setLoaded({ id: activeId });
        setFailure({ id: activeId, text: operationErrorMessage(caught, "实验读取失败") });
      });
    return () => { stopPolling(); };
  }, [activeId, pollRun, stopPolling]);

  const agentNames = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent.name])),
    [agents],
  );

  function selectExperiment(id: string) {
    ++selection.current;
    setActiveId(id);
    setFailure(undefined);
  }

  /** Runs a mutating request and adopts the returned experiment as the new truth. */
  async function mutate(
    request: (id: string) => Promise<Experiment>,
    fallback: string,
    successText: string,
  ) {
    if (!experiment || saving || runningStepIndex !== undefined) return false;
    const selected = selection.current;
    const id = experiment.id;
    setSaving(true);
    setFailure(undefined);
    try {
      const updated = await request(id);
      if (selected !== selection.current) return false;
      setLoaded({ id, experiment: updated });
      setExperiments((current) => upsert(current, summarize(updated)));
      if (successText) message.success(successText);
      return true;
    } catch (caught: unknown) {
      const text = operationErrorMessage(caught, fallback);
      setFailure({ id, text });
      message.error(text);
      return false;
    } finally {
      setSaving(false);
    }
  }

  /** Appends a step and returns its index so the caller can start it in one action. */
  async function addStep(mode: "buyer" | "agent", text: string) {
    if (!experiment) return undefined;
    if (mode === "buyer" && !text.trim()) return undefined;
    const selected = selection.current;
    const box: { created?: Experiment } = {};
    const ok = await mutate(
      async (id) => {
        box.created = await backend.addExperimentStep(id, { mode, text: text.trim() });
        return box.created;
      },
      "步骤添加失败",
      "",
    );
    const created = box.created;
    if (!ok || !created || selected !== selection.current) return undefined;
    const steps = orderedSteps(created);
    return steps[steps.length - 1]?.index;
  }

  async function runStep(stepIndex: number, options: { force?: boolean } = {}) {
    if (!experiment || runningStepIndex !== undefined) return;
    const selected = selection.current;
    const id = experiment.id;
    setFailure(undefined);
    setRunState({ experimentId: id, stepIndex, cancelling: false });
    try {
      const snapshot = await backend.startExperimentRun(id, stepIndex, options.force ?? false);
      if (selected !== selection.current) return;
      if (isRunFinished(snapshot.status)) {
        setRunState({ experimentId: id, run: snapshot, cancelling: false });
        await reload(id, selected);
        return;
      }
      setRunState({ experimentId: id, run: snapshot, stepIndex: snapshot.stepIndex, cancelling: false });
      pollRun(snapshot.runId, id, selected);
    } catch (caught: unknown) {
      if (selected !== selection.current) return;
      if (caught instanceof ExperimentRunConflict) {
        // A run is already going: watch it rather than asking the user to retry.
        setRunState({ experimentId: id, stepIndex, cancelling: false });
        pollRun(caught.runId, id, selected);
        return;
      }
      setRunState(undefined);
      const text = operationErrorMessage(caught, "这一步运行失败");
      setFailure({ id, text });
      message.error(text);
    }
  }

  async function cancelRun() {
    if (!run || cancelling) return;
    const selected = selection.current;
    const id = run.experimentId;
    setRunState((current) => current ? { ...current, cancelling: true } : current);
    try {
      const snapshot = await backend.cancelExperimentRun(run.runId);
      if (selected !== selection.current) return;
      // Cancellation is cooperative: the terminal state still arrives from the
      // next poll, so keep watching rather than declaring the run stopped here.
      setRunState({ experimentId: id, run: snapshot, stepIndex: snapshot.stepIndex, cancelling: false });
    } catch (caught: unknown) {
      if (selected !== selection.current) return;
      setRunState((current) => current ? { ...current, cancelling: false } : current);
      const text = operationErrorMessage(caught, "取消运行失败");
      setFailure({ id, text });
      message.error(text);
    }
  }

  const annotate = (annotations: ExperimentAnnotation[]) =>
    mutate((id) => backend.updateExperiment(id, { annotations }), "批注保存失败", "批注已保存");

  const rename = (name: string) =>
    mutate((id) => backend.updateExperiment(id, { name: name.trim() }), "实验重命名失败", "实验已重命名");

  const removeVariant = (variantId: string) =>
    mutate((id) => backend.removeExperimentVariant(id, variantId), "变体移除失败", "变体已移除");

  /** Re-reads the open experiment without changing the selection. */
  async function refresh() {
    if (!experiment) return;
    const selected = selection.current;
    setFailure(undefined);
    try {
      await reload(experiment.id, selected);
    } catch (caught: unknown) {
      if (selected !== selection.current) return;
      const text = operationErrorMessage(caught, "实验读取失败");
      setFailure({ id: experiment.id, text });
      message.error(text);
    }
  }

  async function promote(variantId: string) {
    if (!experiment) return;
    const selected = selection.current;
    const id = experiment.id;
    setSaving(true);
    setFailure(undefined);
    try {
      await backend.promoteExperimentVariant(id, variantId);
      if (selected !== selection.current) return;
      message.success("已保存为当前版本");
      // Promotion writes the agent preset, not the experiment: re-read the detail
      // so the board shows the version that is now current.
      await reload(id, selected);
    } catch (caught: unknown) {
      if (selected !== selection.current) return;
      const text = operationErrorMessage(caught, "保存为当前版本失败");
      setFailure({ id, text });
      message.error(text);
    } finally {
      setSaving(false);
    }
  }

  async function deleteExperiment(id: string) {
    if (!experiments.some((item) => item.id === id)) return false;
    const selected = selection.current;
    const isActive = id === experiment?.id;
    setSaving(true);
    try {
      await backend.deleteExperiment(id);
      if (isActive && selected !== selection.current) return false;
      const remaining = experiments.filter((item) => item.id !== id);
      setExperiments(remaining);
      if (isActive) {
        stopPolling();
        ++selection.current;
        setActiveId(remaining[0]?.id);
      }
      message.success("实验已删除");
      return true;
    } catch (caught: unknown) {
      const text = operationErrorMessage(caught, "实验删除失败");
      setFailure({ id, text });
      message.error(text);
      return false;
    } finally {
      setSaving(false);
    }
  }

  /** A just-created experiment becomes the selection without a list round trip. */
  function adopt(created: Experiment) {
    ++selection.current;
    setExperiments((current) => upsert(current, summarize(created)));
    setLoaded({ id: created.id, experiment: created });
    setRunState(undefined);
    setFailure(undefined);
    setActiveId(created.id);
  }

  const assignedAgentName = experiment ? agentNames.get(experiment.agentId) ?? experiment.agentId : "";

  return {
    experiments, agentNames, assignedAgentName,
    activeId, experiment, loading, detailLoading, saving,
    runningStepIndex, run, cancelling, error,
    selectExperiment, addStep, runStep, cancelRun, annotate, rename,
    removeVariant, promote, deleteExperiment, adopt, refresh,
  };
}

function upsert(rows: ExperimentSummary[], next: ExperimentSummary) {
  return rows.some((row) => row.id === next.id)
    ? rows.map((row) => (row.id === next.id ? next : row))
    : [next, ...rows];
}
