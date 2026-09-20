import { formatDateTime, nowText } from "@/domain/time";
import type { DataDirStatus, HealthStatus, KeyStatus, NetworkStatus, NodeTestResult, SystemStatusSnapshot, TaskItem, TaskSnapshot, TaskStatus } from "@/types/status";

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

// Mirrors the backend module derivation; the payload carries modules and tasks only.
export function buildSystemStatusSnapshot({
  userStatus,
  proxyStatus,
  receiverStatus,
  dataDirStatus,
  nodeResult,
  taskSnapshots,
  updatedAt = nowText(),
}: {
  userStatus: KeyStatus;
  proxyStatus: NetworkStatus;
  receiverStatus: NetworkStatus;
  dataDirStatus: DataDirStatus;
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
        status: userStatus.has_key && userStatus.db_exists ? "uncertain" : "warning",
        latency: null,
        description: `${userStatus.source} · ${userStatus.ali_id || "未选择"} · 仅检查密钥和源文件存在，不代表解密或同步就绪`,
      },
      {
        id: "health-proxy",
        name: "MITM 代理",
        status: networkToHealth(proxyStatus),
        latency: proxyStatus.latency_ms,
        description: `${proxyStatus.host}:${proxyStatus.port} · 仅 TCP 端口探测，不代表业务就绪${proxyStatus.error ? ` · ${proxyStatus.error}` : ""}`,
      },
      {
        id: "health-receiver",
        name: "MITM Receiver",
        status: networkToHealth(receiverStatus),
        latency: receiverStatus.latency_ms,
        description: `${receiverStatus.host}:${receiverStatus.port} · 仅 TCP 端口探测，不代表业务就绪${receiverStatus.error ? ` · ${receiverStatus.error}` : ""}`,
      },
      {
        id: "health-node",
        name: "MaaFW 上次手动检查",
        status: nodeResult.success ? "healthy" : "warning",
        latency: null,
        description: `上次手动检查：${nodeResult.message} · 仅代表当时界面`,
      },
      {
        id: "health-datadir",
        name: "数据源目录",
        status: dataDirStatus.state === "ok" ? "healthy" : "warning",
        latency: null,
        description: dataDirStatus.path ? `${dataDirStatus.path} · 仅目录检查，不代表数据同步完成` : "尚未配置阿里客户端数据目录，请前往设置页配置",
      },
    ],
    tasks: taskSnapshots.map(taskSnapshotToTaskItem),
  };
}

function networkToHealth(status: NetworkStatus): HealthStatus {
  if (!status.reachable) return "offline";
  if (status.latency_ms !== null && status.latency_ms > 180) return "warning";
  return "uncertain";
}

function formatDuration(startedAt: number | null, completedAt: number | null) {
  if (startedAt === null) return "0s";
  const end = completedAt ?? Date.now() / 1000;
  const seconds = Math.max(0, Math.round(end - startedAt));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}
