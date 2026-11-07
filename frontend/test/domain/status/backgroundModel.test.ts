import { describe, expect, it } from "vitest";
import {
  experimentStatus, latestResources, runtimeRows, runtimeStatus, serviceSummaries, sourceStatus, updateStatus,
} from "@/domain/status/backgroundModel";
import { connectionSnapshot } from "@/mock/connectionData";
import { backgroundStatus, runtimeResource, statusSnapshot, workerObservation } from "@/test/support/backgroundFixture";
import type { BackgroundExperimentRun, BackgroundUpdateStatus } from "@/types/status";

describe("background business evidence", () => {
  it("requires the source version's CRM commit, not a completed source-check loop", () => {
    const source = { ...connectionSnapshot.source, source_revision: 2, applied_source_revision: 1 };
    const snapshot = statusSnapshot({ source, background: backgroundStatus({ runtime: { closing: false, observed_at: 180, resources: [runtimeResource({ name: "im-source-check-1", state: "completed", completed: 5 })] } }) });
    expect(serviceSummaries(snapshot).find((service) => service.id === "sync")?.status.state).toBe("queued");
    expect(sourceStatus({ ...source, applied_source_revision: 2, last_success: null }).state).toBe("waiting");
    expect(sourceStatus({ ...source, applied_source_revision: 2, last_success: 0 }).state).toBe("completed");
    expect(sourceStatus({ ...source, applied_source_revision: 2, observation_stale: true }).state).toBe("unknown");
  });

  it("retains every active same-name instance, even beside a newer ended instance", () => {
    const old = runtimeResource({ started_at: 10, state: "failed", last_error: "old failure" });
    const current = runtimeResource({ started_at: 20, state: "running", active: 1, pending: 2 });
    const concurrent = runtimeResource({ started_at: 20, state: "waiting", pending: 3 });
    const ended = runtimeResource({ started_at: 30, state: "stopped" });
    const resources = [current, old, concurrent, ended];
    expect(latestResources(resources)).toEqual([current, concurrent]);
    expect(runtimeStatus(latestResources(resources)).state).toBe("running");
    expect(runtimeStatus(latestResources(resources)).color).not.toBe("green");
    expect(runtimeRows(resources).map((row) => row.current)).toEqual([true, false, true, false]);
    expect(new Set(runtimeRows(resources).map((row) => row.key)).size).toBe(4);
    const snapshot = statusSnapshot({ background: backgroundStatus({ runtime: { closing: false, observed_at: 180, resources } }) });
    expect(serviceSummaries(snapshot).find((service) => service.id === "crm")?.details).toContain("进程内执行 1 · 排队 5");
    expect(latestResources(resources.map((resource) => ({ ...resource, state: "stopped" })))).toEqual([ended]);
    expect(runtimeStatus([runtimeResource({ state: "completed" })])).toMatchObject({ state: "completed", color: "default" });
  });

  it.each(["im-source-check", "card-sweep"])("excludes ended %s objects after restart without dropping history", (prefix) => {
    const old = runtimeResource({ name: `${prefix}-old`, started_at: 10, state: "failed", last_error: "previous object failure" });
    const current = runtimeResource({ name: `${prefix}-new`, started_at: 20, state: "waiting" });
    const resources = [old, current];
    const snapshot = statusSnapshot({ background: backgroundStatus({ runtime: { closing: false, observed_at: 180, resources } }) });
    expect(serviceSummaries(snapshot).find((service) => service.id === (prefix === "card-sweep" ? "cards" : "sync"))?.resources).toEqual([current]);
    expect(runtimeRows(resources).map((row) => row.instanceLabel)).toEqual(["历史实例", "活动实例"]);
    expect(latestResources([old, { ...current, state: "stopped" }])).toEqual([{ ...current, state: "stopped" }]);
  });

  it.each([
    { alive: false, stopping: true, phase: "stopped", last_error: null, expected: "stopped" },
    { alive: true, stopping: true, phase: "waiting", last_error: null, expected: "stopping" },
    { alive: true, stopping: false, phase: "waiting", last_error: "scan failed", expected: "failed" },
    { alive: true, stopping: false, phase: "waiting", last_error: null, expected: "waiting" },
    { alive: true, stopping: false, phase: "sweeping", last_error: "previous scan failed", expected: "running" },
  ])("reports card state $expected from lifecycle and errors, not unenriched counts", ({ expected, ...worker }) => {
    const snapshot = statusSnapshot({ workers: { "card-sweep": workerObservation({ targets: 13, enriched: 2, failed: 11, ...worker }) } });
    const card = serviceSummaries(snapshot).find((service) => service.id === "cards")!;
    expect(card.status.state).toBe(expected);
    expect(card.details).toContain("已富化 2 · 未富化 11");
  });

  it("keeps admission blocking, waiting, stopping and stopped distinct from business queuing", () => {
    for (const state of ["blocked", "waiting", "stopping", "stopped", "failed"] as const) {
      const snapshot = statusSnapshot({ background: backgroundStatus({
        runtime: { closing: state === "stopping", observed_at: 180, resources: [runtimeResource({ name: "maafw-task-queue", state })] },
        queueTasks: [{ queue: "maafw", id: "pending", type: "GUI", status: "queued", createdAt: "now", duration: "0s", message: "waiting" }],
      }) });
      const gui = serviceSummaries(snapshot).find((service) => service.id === "maafw")!;
      expect(gui.status.state).toBe("queued");
      expect(runtimeStatus(gui.resources).state).toBe(state);
    }
  });

  it.each(["selecting", "sweeping"])("shows current-context %s before targets are available", (phase) => {
    const worker = workerObservation({ phase, targets: null, context: connectionSnapshot.account });
    const summary = (observation: typeof worker) => serviceSummaries(statusSnapshot({ workers: { "card-sweep": observation } })).find((service) => service.id === "cards")!;
    expect(summary(worker).status).toMatchObject({ state: "running", label: "补全中" });
    expect(summary({ ...worker, context: null }).status.state).toBe("unknown");
    expect(summary({ ...worker, phase: "waiting", last_error: "selection failed" }).status.state).toBe("failed");
  });

  it("keeps a cancellation request stopping until the experiment actually terminates", () => {
    const run: BackgroundExperimentRun = { runId: "run", experimentId: "exp", stepIndex: 0, status: "running", startedAt: 100, finishedAt: null, variants: [], cancelRequested: true };
    expect(experimentStatus(run).state).toBe("stopping");
    expect(experimentStatus({ ...run, status: "queued" }).state).toBe("stopping");
    expect(experimentStatus({ ...run, status: "cancelled", finishedAt: 180 }).state).toBe("stopped");
    expect(experimentStatus({ ...run, status: "completed", finishedAt: 180 }).state).toBe("completed");
  });

  it.each(["queued", "running"] as const)("prioritizes %s queue tasks and experiments over historical failures", (activeStatus) => {
    const statuses = ["failed", activeStatus] as const;
    const snapshot = statusSnapshot({ background: backgroundStatus({
      queueTasks: statuses.map((status) => ({ id: status, queue: "maafw", status, type: "GUI", createdAt: "now", duration: "1s", message: status })),
      experiments: statuses.map((status) => ({ runId: status, experimentId: "experiment", status, stepIndex: 0, startedAt: 100, finishedAt: status === "failed" ? 150 : null, variants: [] })),
    }) });
    const summaries = serviceSummaries(snapshot);
    for (const id of ["maafw", "experiments"]) {
      const service = summaries.find((item) => item.id === id)!;
      expect(service.status.state).toBe(activeStatus);
      expect(service.details.join()).toContain("失败 1");
    }
  });

  it.each([
    ["running", "running"], ["awaiting_confirmation", "waiting"], ["queued_send", "queued"],
  ])("prioritizes Outbox %s while retaining unknown and failure counts", (activeStatus, expected) => {
    const snapshot = statusSnapshot({ background: backgroundStatus({ outbox: { counts: { failed: 5, unknown: 2, [activeStatus]: 1 }, recent: [] } }) });
    const outbox = serviceSummaries(snapshot).find((service) => service.id === "outbox")!;
    expect(outbox.status.state).toBe(expected);
    expect(outbox.details.join()).toContain("结果未知 2 · 失败 5");
  });

  it("does not report prepared downloads or an install handoff as a completed update", () => {
    const update: BackgroundUpdateStatus = { supported: true, reason: null, phase: "ready", auto: true, downloaded_bytes: 100, total_bytes: 100, error: null };
    expect(updateStatus(update).state).toBe("waiting");
    expect(updateStatus({ ...update, error: "installation failed" }).state).toBe("failed");
    expect(updateStatus({ ...update, phase: "installing" }).state).toBe("running");
    expect(updateStatus({ ...update, phase: "future-phase" }).state).toBe("unknown");
  });

  it("does not give local Outbox matches a business-success summary", () => {
    const snapshot = statusSnapshot({ background: backgroundStatus({ outbox: { counts: { observed: 12 }, recent: [] } }) });
    const outbox = serviceSummaries(snapshot).find((service) => service.id === "outbox")!;
    expect(outbox.status.state).not.toBe("completed");
    expect(outbox.status.color).not.toBe("green");
    expect(outbox.status.label).toBe("本地已有匹配记录");
  });

  it("keeps unavailable data unknown without hiding unaffected observations", () => {
    const snapshot = statusSnapshot({
      source: connectionSnapshot.source,
      workers: { "card-sweep": workerObservation({ targets: null, visited: null, enriched: null, failed: null }) },
      background: backgroundStatus({ errors: { outbox: "database unavailable", update: "updater unavailable" } }),
    });
    const services = serviceSummaries(snapshot);
    expect(services.find((service) => service.id === "sync")?.status.state).toBe("completed");
    for (const id of ["outbox", "update"]) {
      const service = services.find((item) => item.id === id)!;
      expect(service.status.state).toBe("unknown");
      expect(service.details.join()).not.toContain(" 0");
    }
    expect(services.find((service) => service.id === "cards")?.details.join()).toContain("已富化 未提供");
    expect(services.find((service) => service.id === "crm")?.status.label).toBe("未启动或未登记");
    const legacy = serviceSummaries(statusSnapshot());
    expect(legacy.every((service) => service.status.state === "unknown")).toBe(true);
  });
});
