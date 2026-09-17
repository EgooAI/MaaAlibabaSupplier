import type { DataDirStatus } from "./status";

export type AccountEpoch = string;

export interface ConnectionSnapshot {
  account: { self_ali_id: string; data_dir: string; epoch: AccountEpoch };
  data_dir: DataDirStatus;
  source: {
    phase: "idle" | "syncing" | "ready" | "error";
    ready: boolean;
    error_code: string | null;
    last_error: string | null;
    last_success: number | null;
    key_validation: "unverified" | "valid" | "invalid" | "unavailable";
    epoch: AccountEpoch;
    self_ali_id: string;
    revision?: number;
    source_mtime?: number | null;
    cache_time?: number;
    stale?: boolean;
  };
  client: { connected: boolean; window_generation: string; confirmed: boolean; detail: string };
  capabilities: { read_chat: boolean; use_ai: boolean; operate_client: boolean };
  model: { configured: boolean; verified: false };
  steps: Array<{ id: string; state: "ready" | "pending" | "error"; detail: string }>;
}
