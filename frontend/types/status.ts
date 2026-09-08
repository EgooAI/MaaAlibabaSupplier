import type { ID } from "@/types/common";

export type HealthStatus = "healthy" | "warning" | "offline";
export type TaskStatus = "queued" | "running" | "succeeded" | "failed";
export type DocumentTaskStatus = "pending" | "running" | "succeeded" | "failed";

export interface KeyStatus {
  has_key: boolean;
  source: string;
  ali_id: string;
  db_exists: boolean;
}

export interface NetworkStatus {
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
  id: ID;
  name: string;
  status: HealthStatus;
  latency: number | null;
  description: string;
}

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

export interface SystemStatusSnapshot {
  updatedAt: string;
  modules: HealthModule[];
  tasks: TaskItem[];
  userStatus?: KeyStatus;
  proxyStatus?: NetworkStatus;
  receiverStatus?: NetworkStatus;
  nodeResult?: NodeTestResult;
  taskSnapshots?: TaskSnapshot[];
}

export interface CreateTestTaskInput {
  type: string;
  target: string;
}
