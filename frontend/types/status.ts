import type { ID } from "@/types/common";
import type { SourceSyncStatus } from "./connection";

export type HealthStatus = "healthy" | "warning" | "offline" | "uncertain";
export type TaskStatus = "queued" | "running" | "succeeded" | "failed";
export type DocumentTaskStatus = "pending" | "running" | "succeeded" | "failed";

export interface KeyStatus {
  key_validation?: SourceSyncStatus["key_validation"];
  last_observed_at?: number | null;
  observation_stale?: boolean;
  has_key: boolean;
  source: string;
  ali_id: string;
  db_exists: boolean;
}

export interface NetworkStatus {
  observed_at?: number;
  evidence?: "tcp_connect";
  business_ready?: boolean | null;
  reachable: boolean;
  host: string;
  port: number;
  latency_ms: number | null;
  error: string | null;
}

export interface NodeTestResult {
  success: boolean;
  message: string;
}

export type NodeTestEntry = "ChatInput_GoToInput" | "ContactSearch_GoToSearch";

export interface NodeTestSubmission {
  success: null;
  message: string;
  task_snapshot: TaskSnapshot;
}

export interface TaskSnapshot {
  task_id: ID;
  description: string;
  status: DocumentTaskStatus;
  message: string;
  result: [boolean, string] | null;
  target?: string;
  created_at: number;
  started_at: number | null;
  completed_at: number | null;
}

export interface HealthModule {
  observedAt?: number | null;
  evidence?: string;
  id: HealthModuleId;
  name: string;
  status: HealthStatus;
  latency: number | null;
  description: string;
}

export type HealthModuleId = "health-identity" | "health-proxy" | "health-receiver" | "health-node" | "health-datadir";

export interface TaskItem {
  id: ID;
  type: string;
  status: TaskStatus;
  createdAt: string;
  duration: string;
  target?: string;
  message: string;
  result?: string;
  resultSuccess?: boolean;
}

export interface WorkerObservation {
  started: boolean;
  alive: boolean;
  stopping: boolean;
  started_at: number | null;
  heartbeat_at: number | null;
  last_progress_at: number | null;
  observed_at: number;
  phase: string;
  phase_started_at: number | null;
  phase_age_s: number | null;
  completed_iterations: number;
  pending: number | null;
  pending_observed_at?: number | null;
  context?: { epoch: string; self_ali_id: string; data_dir: string } | null;
  last_error: string | null;
  progress_unit: string;
}

export interface QueueObservation {
  initialized: boolean;
  alive: boolean;
  pending: number;
  current_started: number | null;
  current_age_s: number | null;
  last_completed: number | null;
  observed_at: number;
}

export interface SystemStatusSnapshot {
  queues?: Record<string, QueueObservation>;
  workers?: Record<string, WorkerObservation | null>;
  source?: SourceSyncStatus | null;
  observedAt?: number;
  context?: { epoch: string; self_ali_id: string; data_dir: string };
  lastDiagnostic?: (NodeTestResult & { entry: NodeTestEntry; context: { epoch: string; self_ali_id: string; data_dir: string }; window_generation: string; completed_at: number; currentContext: boolean }) | null;
  updatedAt: string;
  modules: HealthModule[];
  tasks: TaskItem[];
}

export type DataDirState = "unconfigured" | "invalid" | "ok";

export interface DataDirStatus {
  state: DataDirState;
  path: string;
  source: string;
  detail: string;
}

export interface DataDirCandidates {
  candidates: string[];
}

export interface AliAccount {
  ali_id: string;
  db_size: number;
  last_modified: number;
  has_key: boolean;
  key_preview: string;
  key_source: string;
  is_active: boolean;
}

export interface AliIdList {
  accounts: AliAccount[];
  selected: string;
}

export interface CreateTestTaskInput {
  type: string;
  target: string;
}
