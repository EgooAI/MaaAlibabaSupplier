// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountProvider, useAccount } from "@/features/account/AccountProvider";
import { SuggestionPanel } from "@/features/chat/suggestions/SuggestionPanel";
import { useSuggestions } from "@/features/chat/suggestions/useSuggestions";
import { suggestionKey } from "@/features/chat/suggestions/suggestionStorage";
import { connectionSnapshot } from "@/mock/connectionData";
import { accountSession } from "@/services/accountSession";
import { ApiError } from "@/services/httpAdapter";
import { adaptConversationDetail } from "@/services/chatAdapter";
import { authenticatedSession } from "@/test/support/authFixture";
import type { DbAgentPreset } from "@/types/agent";
import type { ConversationDetail } from "@/types/chatCanonical";
import type { SuggestionRun, SuggestionRunInput } from "@/types/suggestions";

const mocks = vi.hoisted(() => ({ backend: {
  getConnection: vi.fn(), listSuggestionAgents: vi.fn(), listSuggestionRuns: vi.fn(), listAgentTools: vi.fn(),
  startSuggestionRun: vi.fn(), getSuggestionRun: vi.fn(), adoptSuggestion: vi.fn(), saveAgentPreset: vi.fn(), listAgentPresetRevisions: vi.fn(),
}, insert: vi.fn(), message: { warning: vi.fn(), error: vi.fn(), success: vi.fn() } }));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", async (original) => ({ ...await original<typeof import("antd")>(), App: { useApp: () => ({ message: mocks.message }) } }));

let root: Root;
let container: HTMLDivElement;
let work: ReturnType<typeof useSuggestions>;
let detail: ConversationDetail;
let agents: DbAgentPreset[];
let runs: Map<string, SuggestionRun>;
let draftId: string;

function Workspace() {
  const value = useSuggestions(detail, () => ({ id: draftId, content: "merchant draft", clear: () => {} }), mocks.insert);
  useEffect(() => { work = value; }, [value]);
  return value.open ? <SuggestionPanel workspace={value} canUseAi blocked={false} /> : null;
}
function Harness() {
  const account = useAccount();
  return account.snapshot && !account.blocked ? <Workspace key={`${account.snapshot.account.epoch}:${account.generation}:${detail.id}`} /> : null;
}
const mount = async () => { await act(async () => root.render(<AccountProvider><Harness /></AccountProvider>)); };
const tick = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); };
const reload = async () => { await act(async () => root.unmount()); root = createRoot(container); await mount(); };
const edit = async (prompt: string) => { await act(async () => work.changeDraft({ ...work.draft!, prompt })); };
const complete = async () => {
  const current = runs.get(work.pending!.runId)!;
  current.status = "completed";
  current.result = { items: [{ id: "candidate", title: "交期", content: "When do you need them?", zh: "您何时需要？", tone: "friendly" }], reasoning: "数量已知，询问交期。", restricted: false, toolCalls: [] };
  await tick();
};

beforeEach(async () => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  localStorage.clear();
  accountSession.clear();
  await authenticatedSession();
  draftId = "original-draft";
  agents = ["default", "custom"].map((id) => ({ apid: id, category: "regular", name: `${id} Agent`, description: "", prompt: `${id} prompt`, intelevel: 0, tools: ["reply_customer"], enabled: true, requires_tool: true, updated_at: "", fingerprint: `${id}-v1` }));
  detail = adaptConversationDetail({ sid: 42, name: "Buyer", participants: [], messages: [], latest: { content: "", updated_at: null }, unread_count: 0, reply_state: "waiting_customer", pending_since: null, history_pending: false, uncertain: false, read_snapshot: "snapshot" });
  detail.messages = [{ id: "one", externalMid: "external:one", content: "Need 500 tents", role: "buyer", createdAt: "2026-11-11 14:32:00" }];
  runs = new Map();
  mocks.backend.getConnection.mockResolvedValue({ ...structuredClone(connectionSnapshot), capabilities: { read_chat: true, use_ai: true, operate_client: true }, client: { ...connectionSnapshot.client, connected: true } });
  const template = { ...agents[0], prompt: "Built-in reply template" };
  mocks.backend.listSuggestionAgents.mockImplementation(async () => structuredClone({ defaultAgentId: "default", agents, template }));
  mocks.backend.listAgentTools.mockResolvedValue([{ name: "reply_customer", title: "回复客户" }]);
  mocks.backend.listAgentPresetRevisions.mockResolvedValue([]);
  mocks.backend.listSuggestionRuns.mockImplementation(async (sid) => structuredClone([...runs.values()].filter((run) => run.conversationId === sid).reverse().slice(0, 2)));
  mocks.backend.startSuggestionRun.mockImplementation(async (sid: string, input: SuggestionRunInput) => {
    const existing = runs.get(input.runId);
    if (existing) return structuredClone(existing);
    const agent = agents.find((item) => item.apid === input.agentId)!;
    const run: SuggestionRun = {
      id: input.runId, request: structuredClone(input), agentId: agent.apid, agentName: agent.name,
      conversationId: sid, createdAt: Date.now() / 1000, status: "queued", error: null, result: null,
      pinned: { kind: input.draft?.prompt === agent.prompt ? "preset" : "draft", prompt: input.draft?.prompt ?? agent.prompt },
      context: input.contextRunId ? runs.get(input.contextRunId)!.context : { id: input.runId, lastMessageId: detail.messages.at(-1)!.externalMid!, lastMessageAt: detail.messages.at(-1)!.createdAt, capturedAt: Date.now() / 1000, messageCount: detail.messages.length },
    };
    runs.set(run.id, run);
    return structuredClone(run);
  });
  mocks.backend.getSuggestionRun.mockImplementation(async (sid, id) => {
    const run = runs.get(id);
    if (!run || run.conversationId !== sid) throw new ApiError("not found", "suggestions", { status: 404 });
    return structuredClone(run);
  });
  mocks.backend.adoptSuggestion.mockImplementation(async (_sid, id, index) => ({ ...runs.get(id)!.result!.items[index], intentId: `intent-${id}`, status: "requested" }));
  mocks.backend.saveAgentPreset.mockImplementation(async (preset) => {
    const saved = { ...preset, fingerprint: `${preset.apid}-v2` };
    agents = [...agents.filter((item) => item.apid !== saved.apid), saved];
    return structuredClone(saved);
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("inline suggestion iteration", () => {
  it("replaces an unavailable remembered selection with an eligible ordinary Agent", async () => {
    agents[0].enabled = false;
    agents.push({ ...agents[1], apid: "system", category: "system" }, { ...agents[1], apid: "no-reply", tools: [] });
    localStorage.setItem(suggestionKey(connectionSnapshot.account, "agent"), "default");
    await mount();
    await act(async () => work.show(true));
    expect(work.catalog?.agents.map((agent) => agent.apid)).toEqual(["custom"]);
    expect(work.agentId).toBe("custom");
    expect(mocks.backend.startSuggestionRun.mock.calls[0][1].agentId).toBe("custom");
  });

  it("runs a selected ordinary agent, tests unsaved edits on fixed context, saves and inserts", async () => {
    await mount();
    await act(async () => work.show());
    await act(async () => { work.selectAgent("custom"); work.setEditing(true); });
    await edit("Ask for the delivery date, not the known quantity.");
    await act(async () => work.generate());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(container.textContent).toContain("正在生成建议");
    const first = mocks.backend.startSuggestionRun.mock.calls[0][1] as SuggestionRunInput;
    expect(first).toMatchObject({ agentId: "custom", draft: { prompt: "Ask for the delivery date, not the known quantity." }, contextRunId: null });
    expect(mocks.backend.saveAgentPreset).not.toHaveBeenCalled();
    await edit("Use a shorter sentence.");
    await complete();
    expect(work.draft?.prompt).toBe("Use a shorter sentence.");
    expect(work.current?.pinned.prompt).toBe(first.draft!.prompt);
    expect(container.textContent).toContain("When do you need them?");
    await act(async () => work.generate());
    expect(mocks.backend.startSuggestionRun.mock.calls[1][1]).toMatchObject({ contextRunId: first.runId, draft: { prompt: "Use a shorter sentence." } });
    await complete();
    expect(container.textContent).toContain("对比上一轮");
    await act(async () => work.save());
    expect(mocks.backend.saveAgentPreset).toHaveBeenCalledWith(expect.objectContaining({ apid: "custom", prompt: "Use a shorter sentence.", expected_fingerprint: "custom-v1" }));
    expect(work.dirty).toBe(false);
    await act(async () => work.adopt(work.current!, 0));
    expect(mocks.insert).toHaveBeenCalledWith(expect.objectContaining({ content: "When do you need them?", intentId: `intent-${work.current!.id}` }));
    await reload();
    await act(async () => work.show(true));
    expect(work.agentId).toBe("custom");
    expect(work.draft?.prompt).toBe("Use a shorter sentence.");
    expect(mocks.backend.startSuggestionRun).toHaveBeenCalledTimes(2);
  });

  it("recovers a lost submission after reload with the same frozen request despite newer edits", async () => {
    await mount();
    await act(async () => work.show());
    mocks.backend.startSuggestionRun.mockRejectedValueOnce(new Error("network timeout"));
    await act(async () => work.generate());
    const first = mocks.backend.startSuggestionRun.mock.calls[0][1];
    await edit("Newer prompt that must not replace the pending request");
    await reload();
    await act(async () => work.show(true));
    expect(mocks.backend.startSuggestionRun).toHaveBeenCalledTimes(1);
    expect(work.draft?.prompt).toContain("Newer prompt");
    await act(async () => work.recover());
    expect(mocks.backend.startSuggestionRun.mock.calls[1][1]).toEqual(first);
    await complete();
    expect(work.current?.pinned.prompt).toBe(first.draft.prompt);
    expect(work.draft?.prompt).toContain("Newer prompt");
  });

  it("reads a completed lost receipt without calling the model endpoint again", async () => {
    await mount();
    await act(async () => work.show(true));
    const id = work.pending!.runId;
    await reload();
    await act(async () => work.show(true));
    expect(work.pending?.runId).toBe(id);
    await complete();
    expect(work.current?.status).toBe("completed");
    expect(mocks.backend.startSuggestionRun).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(suggestionKey(connectionSnapshot.account, "pending:42"))).toBeNull();
  });

  it("refreshes conversation context only on explicit request", async () => {
    await mount();
    await act(async () => work.show(true));
    await complete();
    const first = work.current!;
    detail = { ...detail, messages: [...detail.messages, { id: "two", externalMid: "external:two", role: "buyer", content: "December", createdAt: "2026-11-11 14:40:00" }] };
    await mount();
    expect(work.newMessages).toBe(true);
    await act(async () => work.generate());
    expect(mocks.backend.startSuggestionRun.mock.calls[1][1].contextRunId).toBe(first.id);
    await complete();
    await act(async () => work.generate(true));
    expect(mocks.backend.startSuggestionRun.mock.calls[2][1].contextRunId).toBeNull();
    await complete();
    expect(work.newMessages).toBe(false);
  });

  it("preserves message edits made while candidate adoption is waiting", async () => {
    await mount();
    await act(async () => work.show(true));
    await complete();
    let finish!: (value: unknown) => void;
    mocks.backend.adoptSuggestion.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    let adoption!: Promise<void>;
    await act(async () => { adoption = work.adopt(work.current!, 0); });
    draftId = "new-draft-version";
    await act(async () => { finish({ content: "old candidate", intentId: "intent" }); await adoption; });
    expect(mocks.insert).not.toHaveBeenCalled();
    expect(work.notice).toContain("保留新编辑");
  });

  it.each(["conversation", "account"] as const)("discards delayed adoption after switching %s", async (kind) => {
    await mount();
    await act(async () => work.show(true));
    await complete();
    let finish!: (value: unknown) => void;
    mocks.backend.adoptSuggestion.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    let adoption!: Promise<void>;
    await act(async () => { adoption = work.adopt(work.current!, 0); });
    if (kind === "conversation") { detail = { ...detail, id: "43" }; await mount(); }
    else {
      mocks.backend.listSuggestionRuns.mockResolvedValue([]);
      await act(async () => accountSession.accept({ ...structuredClone(connectionSnapshot), account: { ...connectionSnapshot.account, self_ali_id: "another-seller", epoch: "another-epoch" } }));
    }
    await act(async () => { finish({ content: "old account result", intentId: "old" }); await adoption; });
    expect(mocks.insert).not.toHaveBeenCalled();
    expect(work.current).toBeUndefined();
  });

  it("preserves edits made during save and uses the returned base for the next save", async () => {
    await mount();
    await act(async () => work.show());
    await edit("P2");
    let finish!: (value: DbAgentPreset) => void;
    mocks.backend.saveAgentPreset.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    let saving!: Promise<void>;
    await act(async () => { saving = work.save(); });
    await edit("P3");
    await act(async () => { finish({ ...agents[0], prompt: "P2", fingerprint: "default-v2" }); await saving; });
    expect(work.draft?.prompt).toBe("P3");
    expect(work.dirty).toBe(true);
    await act(async () => work.save());
    expect(mocks.backend.saveAgentPreset.mock.calls[1][0]).toMatchObject({ prompt: "P3", expected_fingerprint: "default-v2" });
  });

  it("clears a rejected submission only after confirming there is no persisted run", async () => {
    await mount();
    await act(async () => work.show());
    mocks.backend.startSuggestionRun.mockRejectedValueOnce(new ApiError("Agent disabled", "suggestions", { status: 409 }));
    await act(async () => work.generate());
    expect(work.pending).toBeUndefined();
    expect(work.error).toContain("Agent disabled");
    await act(async () => { void work.generate(); void work.generate(); });
    expect(mocks.backend.startSuggestionRun).toHaveBeenCalledTimes(2);
    expect(runs.size).toBe(1);
  });

  it("creates a reply Agent from the template even with an empty catalog and loads fresh configuration", async () => {
    agents = [];
    await mount();
    await act(async () => work.show());
    expect(container.textContent).toContain("暂无已启用且具备回复能力");
    await act(async () => work.save(true));
    expect(work.selected).toMatchObject({ category: "regular", enabled: true, tools: ["reply_customer"], requires_tool: true });
    expect(work.agentId).not.toBe("default");
    expect(work.editing).toBe(true);
    const created = agents.find((agent) => agent.apid === work.agentId)!;
    expect(created.prompt).toBe("Built-in reply template");
    created.prompt = "Changed on another page";
    created.fingerprint = "external-v3";
    await act(async () => work.loadSaved());
    expect(work.draft).toMatchObject({ prompt: "Changed on another page", baseFingerprint: "external-v3" });
  });
});
