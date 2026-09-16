import { beforeEach, describe, expect, it } from "vitest";
import { SYSTEM_AGENT_APIDS, agentPresetToDbPreset } from "@/domain/agent/agentModel";
import { mockBackend } from "@/test/support/mockAdapter";

beforeEach(async () => {
  await mockBackend.resetCache();
});

describe("mock adapter", () => {
  it("does not create task snapshots when sending to a missing conversation", async () => {
    const before = await mockBackend.listTaskSnapshots();

    await expect(mockBackend.sendMessage({ conversationId: "missing", content: "hello", action: "send" })).rejects.toThrow("会话不存在");

    const after = await mockBackend.listTaskSnapshots();
    expect(after).toHaveLength(before.length);
  });

  it.each(["send", "test"] as const)("queues %s without fabricating a sent message", async (action) => {
    const before = await mockBackend.getConversation("42");

    const result = await mockBackend.sendMessage({ conversationId: "42", content: "draft only", action });
    const after = await mockBackend.getConversation("42");

    expect(result.execution).toMatchObject({ success: null, task_snapshot: { status: "pending", result: null, started_at: null, completed_at: null } });
    expect(result.message).toBeUndefined();
    expect(result.conversation).toEqual(before);
    expect(after).toEqual(before);
    const status = await mockBackend.getSystemStatus();
    expect(status.taskSnapshots[0]).toEqual(result.execution.task_snapshot);
    expect(status.tasks[0]).toMatchObject({ id: result.execution.task_snapshot.task_id, status: "queued" });
  });

  it.each([undefined, "ContactSearch_GoToSearch"] as const)("queues node test %s and keeps the last completed result", async (entry) => {
    const before = await mockBackend.getSystemStatus();
    const submitted = await mockBackend.runNodeTest(entry);
    expect(submitted).toMatchObject({ success: null, task_snapshot: { status: "pending", result: null, target: entry ?? "ChatInput_GoToInput" } });
    const taskId = submitted.task_snapshot.task_id;
    submitted.task_snapshot.message = "mutated by caller";

    const after = await mockBackend.getSystemStatus();
    expect(after.nodeResult).toEqual(before.nodeResult);
    expect(after.taskSnapshots[0]).toMatchObject({ task_id: taskId, status: "pending", started_at: null, completed_at: null });
    expect(after.taskSnapshots[0].message).not.toBe("mutated by caller");
    expect(after.tasks[0]).toMatchObject({ id: taskId, status: "queued" });
  });

  it("returns cloned status snapshots instead of exposing the store", async () => {
    const first = await mockBackend.getSystemStatus();
    first.tasks[0].message = "mutated by caller";

    const second = await mockBackend.getSystemStatus();
    expect(second.tasks[0].message).not.toBe("mutated by caller");
  });

  it("checks system agent enabled state before translation", async () => {
    const state = await mockBackend.getAgentConsole();
    const preset = state.agentPresets?.find((item) => item.id === SYSTEM_AGENT_APIDS.translation);
    if (!preset) throw new Error("translation agent missing");

    await mockBackend.saveAgentPreset(agentPresetToDbPreset({ ...preset, enabled: false }));

    await expect(mockBackend.requestTranslations({ texts: ["hello"] })).rejects.toThrow("系统 Agent 不存在或未启用");
  });
});
