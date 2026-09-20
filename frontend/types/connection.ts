import type { DataDirStatus } from "./status";

export type AccountEpoch = string;

export interface SourceSyncStatus {
  phase: "idle" | "syncing" | "ready" | "error";
  ready: boolean;
  error_code: string | null;
  last_error: string | null;
  last_success: number | null;
  key_validation: "unverified" | "verifying" | "valid" | "invalid" | "unavailable";
  epoch: AccountEpoch;
  self_ali_id: string;
  revision: number;
  source_revision: number;
  applied_source_revision: number;
  stale: boolean;
  last_checked: number | null;
  last_attempt: number | null;
  retry_at: number | null;
  counts: { inserted: number; updated: number; unchanged: number };
  pending: boolean;
  syncing: boolean;
  freshness: "fresh" | "syncing" | "stale";
  auto_enabled: boolean;
  source_dirty: boolean;
}

export interface ConnectionSnapshot {
  account: { self_ali_id: string; data_dir: string; epoch: AccountEpoch };
  data_dir: DataDirStatus;
  source: SourceSyncStatus;
  client: { connected: boolean; window_generation: string; detail: string };
  capabilities: { read_chat: boolean; use_ai: boolean; operate_client: boolean };
  model: { configured: boolean };
}
