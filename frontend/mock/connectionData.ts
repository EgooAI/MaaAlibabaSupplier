import type { ConnectionSnapshot } from "@/types/connection";

export const connectionSnapshot: ConnectionSnapshot = {
  account: { self_ali_id: "seller-a", data_dir: "D:\\AlibabaSupplierData", epoch: "mock-1" },
  data_dir: { state: "ok", path: "D:\\AlibabaSupplierData", source: "file", detail: "目录有效" },
  source: {
    phase: "ready", ready: true, error_code: null, last_error: null, last_success: 1_789_600_000, key_validation: "valid", epoch: "mock-1", self_ali_id: "seller-a", revision: 1,
    source_revision: 1, applied_source_revision: 1, source_mtime: null, cache_time: 0, stale: false,
    last_checked: 1_789_600_000, last_attempt: 1_789_600_000, retry_at: null,
    counts: { inserted: 10, updated: 0, unchanged: 0 }, pending: false, syncing: false,
    freshness: "fresh", auto_enabled: true, source_dirty: false, wal_frames_applied: 0, last_refresh_ms: 0, wal_pipeline: true,
  },
  client: { connected: false, window_generation: "", confirmed: false, detail: "尚未接入客户端" },
  capabilities: { read_chat: true, use_ai: true, operate_client: false },
  model: { configured: true, verified: false },
  steps: [{ id: "client", state: "pending", detail: "接入客户端后人工确认卖家身份" }],
};
