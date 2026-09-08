import { describe, expect, it } from "vitest";
import { buildSystemStatusSnapshot, taskSnapshotToTaskItem } from "@/domain/status/statusModel";
import type { TaskSnapshot } from "@/types/status";

const task: TaskSnapshot = {
  task_id: "task-1",
  description: "联系人跳转",
  status: "pending",
  message: "等待执行",
  result: null,
  target: "联系人详情页",
  created_at: 1788842400,
  started_at: null,
  completed_at: null,
};

describe("status model adapters", () => {
  it("maps document task snapshot to UI task item", () => {
    const item = taskSnapshotToTaskItem(task);

    expect(item.id).toBe("task-1");
    expect(item.status).toBe("queued");
    expect(item.target).toBe("联系人详情页");
    expect(item.message).toBe("等待执行");
    expect(item.result).toBeUndefined();
  });

  it("maps task result without using message as the result", () => {
    const item = taskSnapshotToTaskItem({ ...task, status: "succeeded", message: "任务完成", result: [true, "跳转成功"] });
    expect(item.message).toBe("任务完成");
    expect(item.result).toBe("跳转成功");
    expect(item.resultSuccess).toBe(true);
  });

  it("keeps unix zero as a valid start time", () => {
    const item = taskSnapshotToTaskItem({ ...task, status: "succeeded", started_at: 0, completed_at: 65 });
    expect(item.duration).toBe("1m 5s");
  });

  it("builds status modules from document status objects", () => {
    const snapshot = buildSystemStatusSnapshot({
      userStatus: { has_key: true, source: ".env", ali_id: "seller-1", db_exists: true },
      proxyStatus: { reachable: true, host: "127.0.0.1", port: 7890, latency_ms: 50, error: null },
      receiverStatus: { reachable: false, host: "127.0.0.1", port: 8788, latency_ms: null, error: "closed" },
      nodeResult: { success: true, message: "ok" },
      taskSnapshots: [task],
      updatedAt: "2026-09-08 10:00",
    });

    expect(snapshot.modules.map((module) => module.status)).toEqual(["healthy", "healthy", "offline", "healthy"]);
    expect(snapshot.modules.map((module) => module.latency)).toEqual([null, 50, null, null]);
    expect(snapshot.tasks[0].status).toBe("queued");
    expect(snapshot.modules[0]).not.toHaveProperty("lastCheckedAt");
    expect(snapshot.receiverStatus?.error).toBe("closed");
  });

  it("only warns for reachable networks with measured high latency", () => {
    const snapshot = buildSystemStatusSnapshot({
      userStatus: { has_key: true, source: ".env", ali_id: "seller-1", db_exists: true },
      proxyStatus: { reachable: true, host: "127.0.0.1", port: 7890, latency_ms: null, error: null },
      receiverStatus: { reachable: true, host: "127.0.0.1", port: 8788, latency_ms: 220, error: null },
      nodeResult: { success: false, message: "down" },
      taskSnapshots: [],
      updatedAt: "2026-09-08 10:00",
    });

    expect(snapshot.modules.map((module) => module.status)).toEqual(["healthy", "healthy", "warning", "offline"]);
  });
});
