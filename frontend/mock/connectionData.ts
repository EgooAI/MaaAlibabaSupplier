import type { ConnectionSnapshot } from "@/types/connection";

export const connectionSnapshot: ConnectionSnapshot = {
  account: { self_ali_id: "seller-a", data_dir: "D:\\AlibabaSupplierData", epoch: "mock-1" },
  data_dir: { state: "ok", path: "D:\\AlibabaSupplierData", source: "file", detail: "目录有效" },
  source: { phase: "ready", ready: true, error_code: null, last_error: null, last_success: 1_789_600_000, key_validation: "valid", epoch: "mock-1", self_ali_id: "seller-a", revision: 1 },
  client: { connected: false, window_generation: "", confirmed: false, detail: "尚未接入客户端" },
  capabilities: { read_chat: true, use_ai: true, operate_client: false },
  model: { configured: true, verified: false },
  steps: [{ id: "client", state: "pending", detail: "接入客户端后人工确认卖家身份" }],
};
