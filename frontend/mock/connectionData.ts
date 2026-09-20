import type { ConnectionSnapshot } from "@/types/connection";

export const connectionSnapshot: ConnectionSnapshot = {
  account: { self_ali_id: "seller-a", data_dir: "D:\\AlibabaSupplierData", epoch: "mock-1" },
  data_dir: { state: "ok", path: "D:\\AlibabaSupplierData", source: "file", detail: "目录有效" },
  source: {
    phase: "ready", ready: true, error_code: null, last_error: null, last_success: 1_789_600_000, key_validation: "valid", epoch: "mock-1", self_ali_id: "seller-a", revision: 1,
    source_revision: 1, applied_source_revision: 1, stale: false,
    last_checked: 1_789_600_000, last_attempt: 1_789_600_000, retry_at: null,
    last_observed_at: 1_789_600_000, observation_stale: false, observation_max_age_s: 30,
    counts: { inserted: 10, updated: 0, unchanged: 0 }, pending: false, syncing: false,
    freshness: "fresh", auto_enabled: true, source_dirty: false,
  },
  client: { connected: false, window_generation: "", detail: "尚未接入客户端" },
  capabilities: { read_chat: true, use_ai: true, operate_client: false },
  model: { configured: true },
};
