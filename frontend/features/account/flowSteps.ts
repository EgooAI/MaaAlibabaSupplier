import type { ConnectionSnapshot } from "@/types/connection";

export interface FlowStepItem {
  key: string;
  title: string;
  content: string;
  status: "finish" | "process" | "error" | "wait";
}

// The backend snapshot stays flat; the presentation pipeline (steps) is derived
// here from the same authoritative fields, so no parallel state exists.
export function flowSteps(snapshot: ConnectionSnapshot): FlowStepItem[] {
  const { account, data_dir: dir, source, client, model } = snapshot;
  const retrying = Boolean(source.last_error && source.retry_at != null && (source.key_validation === "unverified" || source.key_validation === "valid"));
  const syncing = source.syncing || (!retrying && (source.pending || source.phase === "syncing"));
  const keyText = { unverified: retrying ? "暂时无法验证，将自动重试" : "待验证", verifying: "正在验证已保存的密钥", valid: "密钥已验证", invalid: "密钥验证失败", unavailable: "密钥不可用" }[source.key_validation];
  return [
    {
      key: "data_dir",
      title: "数据目录",
      content: dir.state === "ok" ? "目录有效" : dir.detail || "未配置数据目录",
      status: dir.state === "ok" ? "finish" : dir.state === "invalid" ? "error" : "wait",
    },
    {
      key: "identity",
      title: "卖家账号",
      content: account.self_ali_id ? `卖家 ${account.self_ali_id}` : "选择卖家账号",
      status: account.self_ali_id ? "finish" : "wait",
    },
    {
      key: "key",
      title: "密钥",
      content: keyText,
      status: source.key_validation === "valid" ? "finish" : source.key_validation === "verifying" ? "process" : source.key_validation === "unverified" ? "wait" : "error",
    },
    {
      key: "crm",
      title: "聊天同步",
      content: syncing ? "正在同步聊天" : retrying ? "暂时不可用，等待自动重试" : source.last_error || (source.ready ? "聊天已同步" : "待同步"),
      status: syncing ? "process" : retrying ? "wait" : source.phase === "error" || source.last_error ? "error" : source.ready ? "finish" : "wait",
    },
    {
      key: "client",
      title: "客户端接入",
      content: client.connected ? "客户端已接入" : "未接入客户端",
      status: client.connected ? "finish" : "wait",
    },
    {
      key: "model",
      title: "AI 模型",
      content: model.configured ? "模型已配置" : "未配置，前往 Agent → LLM",
      status: model.configured ? "finish" : "wait",
    },
  ];
}
