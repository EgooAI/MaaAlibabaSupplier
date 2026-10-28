import { beforeEach, describe, expect, it } from "vitest";
import { mockBackend } from "@/test/support/mockAdapter";
import type { CreateExperimentInput } from "@/types/experiment";

beforeEach(async () => {
  await mockBackend.resetCache();
});

const comparison: CreateExperimentInput = {
  agentId: "agent-quote",
  source: { kind: "text", text: "客户要求先发货、收货后 60 天付款。" },
  variants: [
    { label: "当前版本", versionKind: "preset" },
    { label: "严格账期", versionKind: "draft", draft: { prompt: "坚持 30% 预付", level: 2, tools: ["calculate"] } },
  ],
};

describe("mock experiments", () => {
  it("compares two versions on one step and keeps their answers apart", async () => {
    const created = await mockBackend.createExperiment(comparison);
    expect(created).toMatchObject({ agentId: "agent-quote", frozenPrefix: "客户要求先发货、收货后 60 天付款。", activeRunId: null, steps: [] });
    expect(created.variants.map((variant) => [variant.label, variant.type, variant.draft ?? false])).toEqual([
      ["当前版本", "playground", false],
      ["严格账期", "playground", true],
    ]);
    expect(created.variants.map((variant) => variant.pinned?.kind)).toEqual(["preset", "draft"]);

    const withStep = await mockBackend.addExperimentStep(created.id, { mode: "buyer", text: "Can you ship within 7 days?" });
    expect(withStep.steps).toEqual([{ index: 0, mode: "buyer", text: "Can you ship within 7 days?", label: "" }]);

    const run = await mockBackend.startExperimentRun(created.id, 0);
    expect(run).toMatchObject({ experimentId: created.id, stepIndex: 0, status: "completed", finishedAt: expect.any(Number) });
    expect(run.variants.map((variant) => variant.status)).toEqual(["completed", "completed"]);
    for (const variant of run.variants) {
      expect(variant.durationMs).toBeGreaterThanOrEqual(50);
      expect(variant.durationMs).toBeLessThanOrEqual(200);
    }

    const detail = await mockBackend.getExperiment(created.id);
    // Every variant saw the same buyer turn and answered it itself.
    for (const variant of detail.variants) {
      expect(variant.messages.map((message) => message.role)).toEqual(["user", "assistant"]);
      expect(variant.messages[0]).toMatchObject({ content: "Can you ship within 7 days?", role: "user" });
    }
    const [first, second] = detail.variants;
    expect(first.messages[1].content).toBe("[当前版本] 对「Can you ship within 7 days?」的回复");
    expect(second.messages[1].content).toBe("[严格账期] 对「Can you ship within 7 days?」的回复");
    expect(first.messages[1].content).not.toBe(second.messages[1].content);
    expect(detail.progress["0"].variants).toEqual(detail.variants.map((variant) => variant.id));
    expect(detail.activeRunId).toBeNull();

    const summaries = await mockBackend.listExperiments("agent-quote");
    expect(summaries).toEqual([
      expect.objectContaining({
        id: created.id,
        variants: [
          expect.objectContaining({ label: "当前版本", turnCount: 2, draft: false }),
          expect.objectContaining({ label: "严格账期", turnCount: 2, draft: true }),
        ],
      }),
    ]);
    expect(await mockBackend.listExperiments("agent-risk")).toEqual([]);
  });

  it("refuses to replay a finished step until asked, and then appends the buyer turn once", async () => {
    const created = await mockBackend.createExperiment(comparison);
    await mockBackend.addExperimentStep(created.id, { mode: "buyer", text: "MOQ?" });
    await mockBackend.addExperimentStep(created.id, { mode: "buyer", text: "Lead time?" });

    await expect(mockBackend.startExperimentRun(created.id, 1)).rejects.toMatchObject({ status: 409, message: "请先运行上一步" });
    await expect(mockBackend.startExperimentRun(created.id, 5)).rejects.toMatchObject({ status: 404, message: "步骤不存在" });
    const first = await mockBackend.startExperimentRun(created.id, 0);
    expect((await mockBackend.getExperimentRun(first.runId)).status).toBe("completed");
    await expect(mockBackend.startExperimentRun(created.id, 0)).rejects.toMatchObject({ status: 409 });

    const forced = await mockBackend.startExperimentRun(created.id, 0, true);
    expect(forced.status).toBe("completed");
    const detail = await mockBackend.getExperiment(created.id);
    for (const variant of detail.variants) {
      expect(variant.messages.map((message) => message.content)).toEqual(["MOQ?", `[${variant.label}] 对「MOQ?」的回复`]);
    }
  });
});
