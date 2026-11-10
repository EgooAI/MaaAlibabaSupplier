import { formatDateTime } from "@/domain/time";
import type { SourceSyncStatus } from "@/types/connection";
import type {
  BackgroundExperimentRun, BackgroundUpdateStatus, RuntimeResource,
  RuntimeState, SystemStatusSnapshot, TaskItem, WorkerObservation,
} from "@/types/status";

export type WorkState = RuntimeState | "queued" | "unknown";
export interface WorkStatus { state: WorkState; label: string; color: string }

const states: Record<WorkState, { label: string; color: string }> = {
  starting: { label: "启动中", color: "blue" },
  queued: { label: "排队中", color: "default" },
  waiting: { label: "等待中", color: "default" },
  blocked: { label: "准入阻塞", color: "orange" },
  running: { label: "执行中", color: "blue" },
  stopping: { label: "停止中", color: "orange" },
  stopped: { label: "已停止", color: "default" },
  failed: { label: "失败", color: "red" },
  completed: { label: "已完成", color: "green" },
  unknown: { label: "未提供", color: "default" },
};

export function workStatus(state: WorkState, label?: string): WorkStatus {
  return { state, ...states[state], ...(label ? { label } : {}) };
}

export const observationTime = (value: number | null | undefined) => value == null ? "尚无记录" : formatDateTime(value);

function isRuntimeActive(resource: RuntimeResource): boolean {
  return !["stopped", "failed", "completed"].includes(resource.state);
}

/** Keeps all active instances, or the latest ended instance when a service is inactive. */
export function latestResources(resources: RuntimeResource[]): RuntimeResource[] {
  const groups = new Map<string, RuntimeResource[]>();
  for (const resource of resources) {
    // These services append an object ID that changes on restart.
    const name = resource.name.replace(/^(im-source-check|card-sweep)-.+$/, "$1");
    const group = groups.get(name) ?? [];
    group.push(resource);
    groups.set(name, group);
  }
  return [...groups.values()].flatMap((group) => {
    const active = group.filter(isRuntimeActive);
    return active.length ? active : [group.reduce((latest, resource) => (resource.started_at ?? -Infinity) >= (latest.started_at ?? -Infinity) ? resource : latest)];
  });
}

export function runtimeRows(resources: RuntimeResource[]) {
  const latest = new Set(latestResources(resources));
  return resources.map((resource, index) => ({
    ...resource,
    key: `${resource.kind}:${resource.name}:${resource.started_at ?? "unstarted"}:${index}`,
    current: latest.has(resource),
    instanceLabel: isRuntimeActive(resource) ? "活动实例" : latest.has(resource) ? "最近结束实例" : "历史实例",
  }));
}

function summarize(statuses: WorkStatus[], empty = "暂无近期任务"): WorkStatus {
  const priority: WorkState[] = ["stopping", "blocked", "running", "starting", "queued", "waiting", "unknown", "failed", "stopped", "completed"];
  return priority.map((state) => statuses.find((status) => status.state === state)).find(Boolean) ?? workStatus("waiting", empty);
}

export function runtimeStatus(resources: RuntimeResource[]): WorkStatus {
  if (!resources.length) return workStatus("unknown", "未启动或未登记");
  const status = summarize(resources.map((resource) => workStatus(resource.state)));
  // A worker can spend its whole lifetime inside a blocking service loop.
  return { ...status, color: status.state === "completed" ? "default" : status.color, label: `执行器${status.label}` };
}

export function taskStatus(task: Pick<TaskItem, "status">): WorkStatus {
  return workStatus(task.status === "succeeded" ? "completed" : task.status);
}

export function experimentStatus(run: BackgroundExperimentRun): WorkStatus {
  if (run.cancelRequested && (run.status === "queued" || run.status === "running")) return workStatus("stopping", "停止中，等待当前调用结束");
  if (run.status === "cancelled") return workStatus("stopped", "已停止（已取消）");
  if (run.status === "interrupted") return workStatus("stopped", "已停止（运行中断）");
  return workStatus(run.status);
}

export function sourceStatus(source: SourceSyncStatus | null | undefined): WorkStatus {
  if (!source) return workStatus("unknown");
  if (source.key_validation === "invalid" || source.key_validation === "unavailable") return workStatus("blocked", "数据源不可用");
  if (source.syncing || source.phase === "syncing" || source.key_validation === "verifying") return workStatus("running", "正在验证或同步");
  if (source.last_error || source.phase === "error") return source.retry_at != null ? workStatus("waiting", "等待重试") : workStatus("failed", "同步失败");
  if (source.pending || source.source_dirty || source.applied_source_revision < source.source_revision) return workStatus("queued", "等待 CRM 提交");
  if (source.observation_stale || source.stale || source.freshness === "stale") return workStatus("unknown", "观察已过期");
  if (source.ready && source.last_success != null) return workStatus("completed", "已提交 CRM");
  return workStatus("waiting", "等待同步");
}

export function updateStatus(update: BackgroundUpdateStatus | null | undefined): WorkStatus {
  if (!update) return workStatus("unknown");
  if (!update.supported) return workStatus("waiting", "当前环境不支持更新");
  if (update.error || update.phase === "error") return workStatus("failed", "更新失败");
  const phases: Record<string, WorkStatus> = {
    idle: workStatus("waiting", "等待检查"),
    checking: workStatus("running", "检查更新中"),
    downloading: workStatus("running", "下载中"),
    ready: workStatus("waiting", "已准备，等待安装"),
    installing: workStatus("running", "安装交接中"),
  };
  return phases[update.phase] ?? workStatus("unknown", `未知阶段：${update.phase}`);
}

function outboxSummary(counts: Record<string, number>): WorkStatus {
  if (counts.navigating > 0 || counts.running > 0 || counts.verifying > 0) return workStatus("running", "执行或核查中");
  if (counts.awaiting_confirmation > 0) return workStatus("waiting", "等待截图确认");
  if (counts.queued > 0 || counts.queued_send > 0) return workStatus("queued");
  if (counts.unknown > 0) return { ...workStatus("unknown", "有结果未知的任务"), color: "orange" };
  if (counts.failed > 0) return workStatus("failed", "有失败任务");
  if (counts.observed > 0) return workStatus("waiting", "本地已有匹配记录");
  if (counts.filled > 0) return workStatus("completed", "已填入，未发送");
  if (counts.cancelled > 0) return workStatus("stopped", "任务已取消");
  return workStatus("waiting", "暂无业务任务");
}

function cardStatus(worker: WorkerObservation | null | undefined): WorkStatus {
  if (!worker) return workStatus("unknown");
  if (!worker.started) return workStatus("waiting", "未启动");
  if (!worker.alive) return workStatus("stopped");
  if (worker.stopping) return workStatus("stopping");
  const scanning = worker.phase === "sweeping" || worker.phase === "selecting";
  if (scanning && (worker.context != null || worker.targets != null)) return workStatus("running", "补全中");
  if (!scanning && worker.last_error) return workStatus("failed", "扫描失败");
  if (worker.targets == null) return workStatus("unknown", "当前工作区暂无扫描记录");
  return workStatus("waiting", "等待手动扫描");
}

export interface ServiceSummary {
  id: string;
  title: string;
  category: "数据处理" | "业务任务" | "运行维护";
  status: WorkStatus;
  details: string[];
  resources: RuntimeResource[];
  error?: string;
}

export function serviceSummaries(snapshot: SystemStatusSnapshot): ServiceSummary[] {
  const background = snapshot.background;
  const resources = latestResources(background?.runtime.resources ?? []);
  const matching = (...names: string[]) => resources.filter((resource) => names.some((name) => resource.name === name || resource.name.startsWith(`${name}-`)));
  const runtimeSummary = (items: RuntimeResource[]) => background ? runtimeStatus(items) : workStatus("unknown");
  const source = snapshot.source;
  const queueSummary = (queue: "maafw" | "translation", title: string): ServiceSummary => {
    const tasks = background?.queueTasks.filter((task) => task.queue === queue) ?? [];
    const fullCounts = background?.queueCounts?.[queue];
    const counts: Record<string, number> | undefined = fullCounts ?? (background ? {
      pending: tasks.filter((task) => task.status === "queued").length,
      running: tasks.filter((task) => task.status === "running").length,
      succeeded: tasks.filter((task) => task.status === "succeeded").length,
      failed: tasks.filter((task) => task.status === "failed").length,
    } : undefined);
    const statuses = counts ? (["queued", "running", "succeeded", "failed"] as const)
      .filter((status) => counts[status === "queued" ? "pending" : status] > 0)
      .map((status) => taskStatus({ status })) : [];
    return {
      id: queue, title, category: "业务任务", resources: matching(`${queue}-task-queue`),
      status: counts ? summarize(statuses) : workStatus("unknown"),
      details: counts ? [
        fullCounts ? "当前账号代次保留期内全量计数" : `仅列表样本 ${tasks.length} 项，全量计数未提供`,
        `排队 ${counts.pending ?? 0} · 执行 ${counts.running ?? 0} · 完成 ${counts.succeeded ?? 0} · 失败 ${counts.failed ?? 0}`,
      ] : ["业务任务数据未提供"],
    };
  };
  const crm = matching("crm-sync");
  const card = snapshot.workers?.["card-sweep"];
  const hasSweep = card?.targets != null;
  const runs = background?.experiments;
  const outboxError = background?.errors.outbox;
  const outboxCounts = outboxError ? undefined : background?.outbox.counts;
  const update = background?.update;
  const updateError = background?.errors.update;
  const basic = matching("mitm-receiver", "yak-logs", "maafw_cli-logs");
  return [
    {
      id: "sync", title: "聊天同步", category: "数据处理", resources: matching("im-source-check"),
      status: sourceStatus(source), error: source?.last_error || undefined,
      details: source ? [`已提交版本 ${source.revision} · 源版本 ${source.source_revision} · 已应用源版本 ${source.applied_source_revision}`, `最近 CRM 提交：${observationTime(source.last_success)}`] : ["同步状态未提供"],
    },
    {
      id: "crm", title: "CRM 写入", category: "数据处理", resources: [], status: runtimeSummary(crm),
      details: crm.length ? [`进程内执行 ${crm.reduce((sum, item) => sum + item.active, 0)} · 排队 ${crm.reduce((sum, item) => sum + item.pending, 0)}`, "CRM 提交版本见“聊天同步”。"] : ["尚无写入执行器记录"],
      error: crm.find((resource) => resource.last_error)?.last_error || undefined,
    },
    queueSummary("maafw", "GUI 队列"),
    queueSummary("translation", "翻译队列"),
    {
      id: "outbox", title: "Outbox 与核查", category: "业务任务", resources: matching("outbox-verifier"), error: outboxError,
      status: outboxCounts ? outboxSummary(outboxCounts) : workStatus("unknown", outboxError ? "读取失败" : undefined),
      details: outboxCounts ? [`排队 ${(outboxCounts.queued ?? 0) + (outboxCounts.queued_send ?? 0)} · 执行 ${(outboxCounts.navigating ?? 0) + (outboxCounts.running ?? 0)} · 待确认 ${outboxCounts.awaiting_confirmation ?? 0}`, `核查中 ${outboxCounts.verifying ?? 0} · 结果未知 ${outboxCounts.unknown ?? 0} · 失败 ${outboxCounts.failed ?? 0}`, "平台送达结果以核对证据为准。"] : ["Outbox 业务数据未提供"],
    },
    {
      id: "cards", title: "卡片清扫服务", category: "数据处理", resources: matching("card-sweep"), error: card?.last_error || undefined,
      status: cardStatus(card),
      details: card ? [`目标会话 ${card.targets ?? "未提供"} · 已访问 ${card.visited ?? "未提供"}`, `已富化 ${card.enriched ?? "未提供"} · 未富化 ${card.failed ?? "未提供"}`, ...(hasSweep ? [`最近一轮：${observationTime(card.last_sweep_at)}`] : [])] : ["卡片补全进度未提供"],
    },
    {
      id: "experiments", title: "Agent 实验", category: "业务任务", resources: [],
      status: runs ? summarize(runs.map(experimentStatus)) : workStatus("unknown"),
      details: runs ? [`当前工作区近期运行 ${runs.length} 项 · 失败 ${runs.filter((run) => run.status === "failed").length}`, "取消请求需等待当前调用结束，以运行快照为准。"] : ["实验运行数据未提供"],
    },
    {
      id: "update", title: "程序更新", category: "运行维护", resources: matching("auto-updater", "manual-updater", "update-handoff"),
      status: updateError ? workStatus("unknown", "读取失败") : updateStatus(update), error: updateError || update?.error || undefined,
      details: update && !updateError ? [update.reason || `自动检查${update.auto ? "已启用" : "未启用"}`, ...(update.phase === "downloading" ? [`已下载 ${update.downloaded_bytes.toLocaleString()} B / ${update.total_bytes == null ? "总量未知" : `${update.total_bytes.toLocaleString()} B`}`] : [])] : ["更新状态未提供"],
    },
    {
      id: "services", title: "基础服务", category: "运行维护", resources: basic, status: runtimeSummary(basic),
      details: ["MITM 接收、Yak 与 MaaFW 日志采集。连接证据见“连接与诊断”。"],
    },
  ];
}
