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

  it("does not return the historical latest message for test actions", async () => {
    const before = await mockBackend.getConversation("42");

    const result = await mockBackend.sendMessage({ conversationId: "42", content: "draft only", action: "test" });
    const after = await mockBackend.getConversation("42");

    expect(result.message).toBeUndefined();
    expect(result.conversation.messages).toHaveLength(before.messages.length);
    expect(after.messages).toHaveLength(before.messages.length);
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
