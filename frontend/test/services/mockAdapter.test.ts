import { beforeEach, describe, expect, it } from "vitest";
import { SYSTEM_AGENT_APIDS, agentPresetToDbPreset } from "@/domain/agent/agentModel";
import { mockBackend } from "@/test/support/mockAdapter";

beforeEach(async () => {
  await mockBackend.resetCache();
  const initial = await mockBackend.getConnection();
  const connected = await mockBackend.connectClient(initial.account.epoch);
  await mockBackend.confirmClient(connected.account.epoch, connected.client.window_generation);
});

describe("mock adapter", () => {
  it("observes without side effects and requires new confirmation on every connection", async () => {
    const initial = await mockBackend.getConnection();
    const beforeTasks = await mockBackend.listTaskSnapshots();
    expect(await mockBackend.getConnection()).toEqual(initial);
    const connected = await mockBackend.connectClient(initial.account.epoch);
    expect(connected.capabilities.operate_client).toBe(false);
    expect(await mockBackend.listTaskSnapshots()).toEqual(beforeTasks);
    await expect(mockBackend.sendMessage({ conversationId: "42", content: "draft", action: "test", idempotency_key: "key" })).rejects.toThrow("人工确认");
    await expect(mockBackend.confirmClient(connected.account.epoch, initial.client.window_generation)).rejects.toThrow("窗口已变化");
    const confirmed = await mockBackend.confirmClient(connected.account.epoch, connected.client.window_generation);
    expect(confirmed.capabilities.operate_client).toBe(true);
  });

  it("revokes confirmation and sync readiness after selecting a different seller", async () => {
    const initial = await mockBackend.getConnection();
    await mockBackend.saveAliId("seller-b", initial.account.epoch);
    const changed = await mockBackend.getConnection();
    expect(changed.account.epoch).not.toBe(initial.account.epoch);
    expect(changed.account.self_ali_id).toBe("seller-b");
    expect(changed.source.phase).toBe("idle");
    expect(changed.client.confirmed).toBe(false);
    await expect(mockBackend.retryConnection(initial.account.epoch)).rejects.toThrow("账号已变化");
    const submitted = await mockBackend.retryConnection(changed.account.epoch);
    expect(submitted.source).toMatchObject({ syncing: true, pending: true, freshness: "syncing", revision: changed.source.revision });
    expect(submitted.capabilities.read_chat).toBe(false);
    await expect.poll(() => mockBackend.getConnection(), { timeout: 2000 }).toMatchObject({
      source: { syncing: false, pending: false, freshness: "fresh", revision: changed.source.revision + 1 },
      capabilities: { read_chat: true, use_ai: true, operate_client: false },
    });
  });

  it("allows diagnostics after connecting without permitting unconfirmed GUI writes", async () => {
    const initial = await mockBackend.getConnection();
    const connected = await mockBackend.connectClient(initial.account.epoch);
    expect(connected.client.confirmed).toBe(false);
    await expect(mockBackend.runNodeTest()).resolves.toMatchObject({ success: null, task_snapshot: { status: "pending" } });
    await expect(mockBackend.sendMessage({ conversationId: "42", content: "hello", action: "test", idempotency_key: "key" })).rejects.toThrow("人工确认");
    await expect(mockBackend.gotoContact("42", "buyer")).rejects.toThrow("人工确认");
  });
  it("does not create task snapshots when sending to a missing conversation", async () => {
    const before = await mockBackend.listTaskSnapshots();

    await expect(mockBackend.sendMessage({ conversationId: "missing", content: "hello", action: "send", idempotency_key: "key" })).rejects.toThrow("会话不存在");

    const after = await mockBackend.listTaskSnapshots();
    expect(after).toHaveLength(before.length);
  });

  it.each(["send", "test"] as const)("queues %s without fabricating a sent message", async (action) => {
    const before = await mockBackend.getConversation("42");

    const input = { conversationId: "42", content: "draft only", action, idempotency_key: "key" };
    const result = await mockBackend.sendMessage(input);
    const after = await mockBackend.getConversation("42");

    expect(result.outbox).toMatchObject({ status: "queued", may_have_sent: false, idempotency_key: "key" });
    expect(after).toEqual(before);
    expect(await mockBackend.sendMessage(input)).toEqual(result);
    expect(await mockBackend.listOutbox("42")).toEqual([result.outbox]);
  });

  it.each([undefined, "ContactSearch_GoToSearch"] as const)("queues node test %s and keeps the last completed result", async (entry) => {
    const before = await mockBackend.getSystemStatus();
    const submitted = await mockBackend.runNodeTest(entry);
    expect(submitted).toMatchObject({ success: null, task_snapshot: { status: "pending", result: null, target: entry ?? "ChatInput_GoToInput" } });
    const taskId = submitted.task_snapshot.task_id;
    submitted.task_snapshot.message = "mutated by caller";

    const after = await mockBackend.getSystemStatus();
    expect(after.modules.find((module) => module.id === "health-node")).toEqual(before.modules.find((module) => module.id === "health-node"));
    expect(after.tasks[0]).toMatchObject({ id: taskId, status: "queued", message: expect.not.stringContaining("mutated by caller") });
  });

  it("returns cloned status snapshots instead of exposing the store", async () => {
    const first = await mockBackend.getSystemStatus();
    first.tasks[0].message = "mutated by caller";

    const second = await mockBackend.getSystemStatus();
    expect(second.tasks[0].message).not.toBe("mutated by caller");
  });

  it("runs the translation job lifecycle with the three-value protocol", async () => {
    const submitted = await mockBackend.requestTranslations({ texts: ["hello", "已经是中文", "[[占位符]]"], force: false, conversationId: 42 });
    expect(submitted).toMatchObject({ status: "pending" });
    await expect.poll(() => mockBackend.getTranslationJob(submitted.task_id), { timeout: 2000 }).toMatchObject({ status: "succeeded", message: "已翻译 3 条" });

    const cached = await mockBackend.queryTranslations({ texts: ["hello", "已经是中文", "[[占位符]]"] });
    // 非空=译文；空串=NO_NEED 哨兵；null=ABNORMAL 未缓存。
    expect(cached.translations).toEqual({ hello: "这是 mock 译文：hello", "已经是中文": "", "[[占位符]]": null });

    const forced = await mockBackend.requestTranslations({ texts: ["hello"], force: true });
    await expect.poll(() => mockBackend.getTranslationJob(forced.task_id), { timeout: 2000 }).toMatchObject({ status: "succeeded" });
    expect((await mockBackend.queryTranslations({ texts: ["hello"] })).translations).toEqual({ hello: "重新翻译 mock 译文：hello" });

    const empty = await mockBackend.requestTranslations({ texts: ["  "] });
    expect(empty).toMatchObject({ task_id: "", status: "succeeded" });
  });

  it("checks system agent enabled state before translation", async () => {
    const state = await mockBackend.getAgentConsole();
    const preset = state.agentPresets?.find((item) => item.id === SYSTEM_AGENT_APIDS.translation);
    if (!preset) throw new Error("translation agent missing");

    await mockBackend.saveAgentPreset(agentPresetToDbPreset({ ...preset, enabled: false }));

    await expect(mockBackend.requestTranslations({ texts: ["hello"] })).rejects.toThrow("系统 Agent 不存在或未启用");
  });
});
