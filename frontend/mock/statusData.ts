import { buildSystemStatusSnapshot, taskSnapshotToTaskItem } from "@/domain/status/statusModel";
import type { KeyStatus, NetworkStatus, NodeTestResult, TaskSnapshot } from "@/types/status";

export const keyStatus: KeyStatus = {
  has_key: true,
  source: ".env",
  ali_id: "seller-ali-001",
  db_exists: true,
};

export const proxyStatus: NetworkStatus = {
  reachable: true,
  host: "127.0.0.1",
  port: 7890,
  latency_ms: 212,
  error: null,
};

export const receiverStatus: NetworkStatus = {
  reachable: true,
  host: "127.0.0.1",
  port: 8788,
  latency_ms: 95,
  error: null,
};

export const nodeResult: NodeTestResult = {
  success: true,
  message: "MaaFW 节点测试通过（Mock）",
};

export const taskSnapshots: TaskSnapshot[] = [
  {
    task_id: "task-001",
    description: "翻译回填",
    status: "running",
    message: "处理 12 条待翻译消息",
    result: null,
    target: "聊天翻译队列",
    created_at: 1788756120,
    started_at: 1788756180,
    completed_at: null,
  },
  {
    task_id: "task-002",
    description: "客户画像刷新",
    status: "succeeded",
    message: "完成 36 个客户标签更新",
    result: [true, "客户画像刷新完成"],
    target: "客户画像",
    created_at: 1788751860,
    started_at: 1788751920,
    completed_at: 1788752084,
  },
  {
    task_id: "task-003",
    description: "代理连通性检查",
    status: "failed",
    message: "新加坡节点超时，已切换备用节点",
    result: [false, "主节点超时"],
    target: "代理节点",
    created_at: 1788749700,
    started_at: 1788749760,
    completed_at: 1788749822,
  },
];

export const tasks = taskSnapshots.map(taskSnapshotToTaskItem);

export const systemStatus = buildSystemStatusSnapshot({
  userStatus: keyStatus,
  proxyStatus,
  receiverStatus,
  nodeResult,
  taskSnapshots,
  updatedAt: "2026-09-07 10:25",
});
