import { formatDateTime, nowText } from "@/domain/time";
import type { HealthModuleId, HealthStatus, KeyStatus, NetworkStatus, NodeTestResult, SystemStatusSnapshot, TaskItem, TaskSnapshot, TaskStatus } from "@/types/status";

export const HEALTH_MODULE_TITLES: Record<HealthModuleId, string> = {
  "health-identity": "用户状态",
  "health-proxy": "MITM 代理",
  "health-receiver": "MITM Receiver",
  "health-node": "MaaFW 节点",
};

export function taskSnapshotToTaskItem(snapshot: TaskSnapshot): TaskItem {
  const result = snapshot.result ?? undefined;
  return {
    id: snapshot.task_id,
    type: snapshot.description,
    status: documentTaskStatusToUi(snapshot.status),
    createdAt: formatDateTime(snapshot.created_at),
    duration: formatDuration(snapshot.started_at, snapshot.completed_at),
    target: snapshot.target,
    message: snapshot.message,
    result: result?.[1],
    resultSuccess: result?.[0],
  };
}

function documentTaskStatusToUi(status: TaskSnapshot["status"]): TaskStatus {
  return status === "pending" ? "queued" : status;
}

export function buildSystemStatusSnapshot({
  userStatus,
  proxyStatus,
  receiverStatus,
  nodeResult,
  taskSnapshots,
  updatedAt = nowText(),
}: {
  userStatus: KeyStatus;
  proxyStatus: NetworkStatus;
  receiverStatus: NetworkStatus;
  nodeResult: NodeTestResult;
  taskSnapshots: TaskSnapshot[];
  updatedAt?: string;
}): SystemStatusSnapshot {
  return {
    updatedAt,
    modules: [
      {
        id: "health-identity",
        name: "身份服务",
        status: userStatus.has_key && userStatus.db_exists ? "healthy" : "warning",
        latency: null,
        description: `${userStatus.source} · ${userStatus.ali_id || "未识别"}`,
      },
      {
        id: "health-proxy",
        name: "代理服务",
        status: networkToHealth(proxyStatus),
        latency: proxyStatus.latency_ms,
        description: `${proxyStatus.host}:${proxyStatus.port}${proxyStatus.error ? ` · ${proxyStatus.error}` : ""}`,
      },
      {
        id: "health-receiver",
        name: "Receiver",
        status: networkToHealth(receiverStatus),
        latency: receiverStatus.latency_ms,
        description: `${receiverStatus.host}:${receiverStatus.port}${receiverStatus.error ? ` · ${receiverStatus.error}` : ""}`,
      },
      {
        id: "health-node",
        name: "MaaFW 节点",
        status: nodeResult.success ? "healthy" : "offline",
        latency: null,
        description: nodeResult.message,
      },
    ],
    tasks: taskSnapshots.map(taskSnapshotToTaskItem),
    userStatus,
    proxyStatus,
    receiverStatus,
    nodeResult,
    taskSnapshots,
  };
}

function networkToHealth(status: NetworkStatus): HealthStatus {
  if (!status.reachable) return "offline";
  if (status.latency_ms !== null && status.latency_ms > 180) return "warning";
  return "healthy";
}

function formatDuration(startedAt: number | null, completedAt: number | null) {
  if (startedAt === null) return "0s";
  const end = completedAt ?? Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - startedAt));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}
