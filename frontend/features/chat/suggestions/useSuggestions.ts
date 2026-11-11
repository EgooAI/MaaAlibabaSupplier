"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AccountChangedError, useAccount, useAccountBackend } from "@/features/account/AccountProvider";
import { ApiError } from "@/services/httpAdapter";
import type { AgentPresetRevision, AgentToolDefinition } from "@/types/agent";
import type { AssistantSuggestion, ConversationDetail } from "@/types/chatCanonical";
import type { SuggestionCatalog, SuggestionRun, SuggestionRunInput } from "@/types/suggestions";
import type { DraftSubmission } from "../outbox/OutboxWorkspace";
import { draftOf, readDraft, readPending, runDraft, sameDraft, suggestionKey, type PromptDraft } from "./suggestionStorage";

const terminal = (run: SuggestionRun) => run.status === "completed" || run.status === "failed";

export function useSuggestions(conversation: ConversationDetail, captureDraft: () => DraftSubmission, insert: (item: AssistantSuggestion) => void) {
  const backend = useAccountBackend();
  const { snapshot, blocked } = useAccount();
  const account = snapshot!.account;
  const sid = conversation.id;
  const pendingKey = suggestionKey(account, `pending:${sid}`);
  const selectionKey = suggestionKey(account, "agent");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [catalog, setCatalog] = useState<SuggestionCatalog>();
  const [agentId, setAgentId] = useState("");
  const [drafts, setDrafts] = useState<Record<string, PromptDraft>>({});
  const [tools, setTools] = useState<AgentToolDefinition[]>([]);
  const [revisions, setRevisions] = useState<AgentPresetRevision[]>([]);
  const [runs, setRuns] = useState<SuggestionRun[]>([]);
  const [pending, setPending] = useState<SuggestionRunInput>();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const [error, setError] = useState<string>();
  const [runError, setRunError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const alive = useRef(true);
  const init = useRef<Promise<SuggestionCatalog> | undefined>(undefined);
  const state = useRef({ catalog, agentId, drafts, runs, pending, captureDraft, insert });
  state.current = { catalog, agentId, drafts, runs, pending, captureDraft, insert };
  const admission = useRef(false);
  const configBusy = useRef(false);
  const adoptionBusy = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const report = useCallback((caught: unknown) => {
    if (alive.current && !(caught instanceof AccountChangedError)) setError(caught instanceof Error ? caught.message : "操作失败，请重试");
  }, []);

  const keepRun = useCallback((run: SuggestionRun) => {
    if (!alive.current || run.conversationId !== sid) return;
    setRuns((current) => {
      const previous = current.find((item) => item.id === run.id);
      if (previous && (terminal(previous) || previous.status === "running" && run.status === "queued")) return current;
      return [run, ...current.filter((item) => item.id !== run.id)].sort((a, b) => b.createdAt - a.createdAt).slice(0, 2);
    });
    if (terminal(run)) {
      setPending((current) => current?.runId === run.id ? undefined : current);
      try { if (readPending(pendingKey)?.runId === run.id) localStorage.removeItem(pendingKey); } catch { /* The next lookup can reconcile a retained receipt. */ }
    }
  }, [pendingKey, sid]);

  const load = useCallback(async () => {
    if (init.current) return init.current;
    setLoading(true);
    init.current = (async () => {
      const [next, recent, availableTools] = await Promise.all([
        backend.listSuggestionAgents(), backend.listSuggestionRuns(sid), backend.listAgentTools(),
      ]);
      if (!alive.current) throw new AccountChangedError();
      let selected = next.defaultAgentId;
      const recovered: Record<string, PromptDraft> = {};
      let request: SuggestionRunInput | undefined;
      try {
        selected = localStorage.getItem(selectionKey) || selected;
        for (const agent of next.agents) {
          const draft = readDraft(suggestionKey({ self_ali_id: account.self_ali_id, data_dir: account.data_dir }, `draft:${agent.apid}`));
          if (draft) recovered[agent.apid] = draft;
        }
        request = readPending(pendingKey);
      } catch { setNotice("本地暂存不可读，请保持页面开启以保留提示词修改。"); }
      request ??= recent.find((run) => !terminal(run))?.request;
      if (!next.agents.some((agent) => agent.apid === selected)) selected = next.defaultAgentId;
      setCatalog(next);
      setAgentId(selected);
      setDrafts(recovered);
      setTools(availableTools);
      setRuns(recent);
      setPending(request);
      state.current = { ...state.current, catalog: next, agentId: selected, drafts: recovered, runs: recent, pending: request };
      return next;
    })();
    try { return await init.current; }
    catch (caught) { init.current = undefined; throw caught; }
    finally { if (alive.current) setLoading(false); }
  }, [backend, sid, selectionKey, pendingKey, account.self_ali_id, account.data_dir]);

  useEffect(() => {
    if (!blocked) void load().catch(report);
  }, [blocked, load, report]);

  async function show(generateIfEmpty = false) {
    setOpen(true);
    try {
      await load();
      if (generateIfEmpty && !state.current.runs.length && !state.current.pending) await generate();
    } catch (caught) { report(caught); }
  }

  async function sendRequest(request: SuggestionRunInput) {
    setSubmitting(true);
    setRunError(undefined);
    try { keepRun(await backend.startSuggestionRun(sid, request)); }
    catch (caught) {
      if (alive.current && !(caught instanceof AccountChangedError)) setRunError(caught instanceof Error ? caught.message : "提交失败，可恢复原请求");
      if (caught instanceof ApiError && caught.status && caught.status >= 400 && caught.status < 500) {
        try { keepRun(await backend.getSuggestionRun(sid, request.runId)); }
        catch (lookup) {
          if (alive.current && lookup instanceof ApiError && lookup.status === 404) {
            setPending((current) => current?.runId === request.runId ? undefined : current);
            try { if (readPending(pendingKey)?.runId === request.runId) localStorage.removeItem(pendingKey); } catch { /* Retains recovery data on storage failure. */ }
          }
        }
      }
    }
    finally { if (alive.current) setSubmitting(false); }
  }

  async function generate(refreshContext = false) {
    if (blocked || admission.current || state.current.pending) return;
    const agent = state.current.catalog?.agents.find((item) => item.apid === state.current.agentId);
    if (!agent) return;
    admission.current = true;
    try {
      const draft = state.current.drafts[agent.apid] ?? draftOf(agent);
      const request: SuggestionRunInput = {
        runId: crypto.randomUUID(), agentId: agent.apid, draft: runDraft(draft),
        contextRunId: refreshContext ? null : state.current.runs[0]?.id ?? null,
      };
      // Persist the exact submission before the network call, including its prompt snapshot.
      localStorage.setItem(pendingKey, JSON.stringify(request));
      state.current.pending = request;
      setPending(request);
      setNotice(undefined);
      await sendRequest(request);
    } catch (caught) { report(caught); }
    finally { admission.current = false; }
  }

  const observe = useCallback(async (request: SuggestionRunInput) => {
    try {
      const run = await backend.getSuggestionRun(sid, request.runId);
      keepRun(run);
      if (alive.current && state.current.pending?.runId === request.runId) setRunError(undefined);
    } catch (caught) {
      if (alive.current && state.current.pending?.runId === request.runId && !(caught instanceof AccountChangedError)) setRunError(caught instanceof ApiError && caught.status === 404
        ? "尚未查到本次生成的受理记录，可恢复原请求。" : caught instanceof Error ? caught.message : "运行结果暂不可读");
    }
  }, [backend, keepRun, sid]);

  useEffect(() => {
    if (!pending || blocked || submitting) return;
    let stopped = false;
    let inFlight = false;
    const poll = async () => {
      if (stopped || document.hidden || inFlight) return;
      inFlight = true;
      try { await observe(pending); } finally { inFlight = false; }
    };
    void poll();
    const timer = setInterval(() => void poll(), 2000);
    return () => { stopped = true; clearInterval(timer); };
  }, [pending, blocked, submitting, observe]);

  function selectAgent(id: string) {
    state.current.agentId = id;
    setAgentId(id);
    setRevisions([]);
    try { localStorage.setItem(selectionKey, id); } catch { setNotice("Agent 选择仅保留在当前页面。"); }
  }

  function changeDraft(next: PromptDraft, id = state.current.agentId) {
    state.current.drafts = { ...state.current.drafts, [id]: next };
    setDrafts((current) => ({ ...current, [id]: next }));
    try { localStorage.setItem(suggestionKey(account, `draft:${id}`), JSON.stringify(next)); }
    catch { setNotice("提示词仅暂存于当前页面，刷新前请保存。"); }
  }

  async function save(copyDefault = false) {
    if (blocked || configBusy.current) return;
    const current = state.current;
    const agent = current.catalog?.agents.find((item) => item.apid === (copyDefault ? current.catalog?.defaultAgentId : current.agentId));
    if (!agent) return;
    const draft = copyDefault ? draftOf(agent) : current.drafts[agent.apid] ?? draftOf(agent);
    const id = copyDefault ? `agent-${crypto.randomUUID().replaceAll("-", "")}` : agent.apid;
    configBusy.current = true;
    setSaving(true);
    setError(undefined);
    try {
      const saved = await backend.saveAgentPreset({
        ...agent, apid: id, prompt: draft.prompt, intelevel: draft.level, tools: copyDefault ? [...new Set([...draft.tools, "reply_customer"])] : draft.tools,
        requires_tool: copyDefault || draft.requires_tool, name: copyDefault ? "自定义回复助手" : agent.name,
        category: copyDefault ? "regular" : agent.category, enabled: copyDefault || agent.enabled,
        expected_fingerprint: copyDefault ? undefined : draft.baseFingerprint,
        revision_note: "在聊天建议面板中调整",
      });
      if (!alive.current) return;
      setCatalog((value) => value && ({ ...value, agents: [...value.agents.filter((item) => item.apid !== id), saved] }));
      const latest = state.current.drafts[id];
      changeDraft(latest && !sameDraft(latest, draft) ? { ...latest, baseFingerprint: saved.fingerprint } : draftOf(saved), id);
      if (copyDefault) { selectAgent(id); setEditing(true); }
      setNotice(copyDefault ? "已创建并选中回复助手，可直接修改提示词。" : "提示词已保存，之后使用该 Agent 的建议将采用新版本。");
    } catch (caught) { report(caught); }
    finally { configBusy.current = false; if (alive.current) setSaving(false); }
  }

  async function loadRevisions() {
    const id = state.current.agentId;
    try {
      const result = await backend.listAgentPresetRevisions(id);
      if (alive.current && state.current.agentId === id) setRevisions(result);
    } catch (caught) { report(caught); }
  }

  async function loadSaved() {
    const id = state.current.agentId;
    const original = state.current.drafts[id];
    try {
      const next = await backend.listSuggestionAgents();
      if (!alive.current) return;
      setCatalog(next);
      const saved = next.agents.find((agent) => agent.apid === id);
      if (saved && state.current.drafts[id] === original) changeDraft(draftOf(saved), id);
    } catch (caught) { report(caught); }
  }

  async function adopt(run: SuggestionRun, index: number) {
    if (blocked || adoptionBusy.current) return;
    const original = state.current.captureDraft().id;
    adoptionBusy.current = true;
    setAdopting(true);
    setError(undefined);
    try {
      const item = await backend.adoptSuggestion(sid, run.id, index);
      if (!alive.current) return;
      if (state.current.captureDraft().id !== original) {
        setNotice("等待期间回复草稿已更新，已保留新编辑；需要替换时请再次点击插入。");
        return;
      }
      state.current.insert(item);
      setNotice("已插入回复框，可继续编辑后发送。");
    } catch (caught) { report(caught); }
    finally { adoptionBusy.current = false; if (alive.current) setAdopting(false); }
  }

  const selected = catalog?.agents.find((agent) => agent.apid === agentId);
  const draft = selected ? drafts[agentId] ?? draftOf(selected) : undefined;
  const current = runs[0];
  const newMessages = Boolean(current && conversation.messages.at(-1)?.externalMid !== current.context.lastMessageId);
  return {
    open, setOpen, editing, setEditing, show, catalog, selected, agentId, selectAgent, draft, changeDraft,
    dirty: Boolean(selected && draft && !sameDraft(draft, draftOf(selected))), tools, revisions, loadRevisions,
    runs, current, pending, loading, submitting, saving, adopting, error: error ?? runError, notice, newMessages, generate, save, adopt, loadSaved,
    loadAgents: async () => { try { await load(); } catch (caught) { report(caught); } },
    recover: async () => { if (pending && !admission.current) { admission.current = true; try { await sendRequest(pending); } finally { admission.current = false; } } },
  };
}
