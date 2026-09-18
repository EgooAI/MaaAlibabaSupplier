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
  const keyText = { unverified: "待验证", valid: "密钥已验证", invalid: "密钥验证失败", unavailable: "密钥不可用" }[source.key_validation];
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
      status: source.key_validation === "valid" ? "finish" : source.key_validation === "unverified" ? "wait" : "error",
    },
    {
      key: "crm",
      title: "聊天同步",
      content: source.ready ? "聊天已同步" : source.last_error || "待同步",
      status: source.ready ? "finish" : source.phase === "error" || source.last_error ? "error" : "wait",
    },
    {
      key: "client",
      title: "客户端确认",
      content: client.connected ? (client.confirmed ? "已确认卖家" : "待人工确认") : "未接入客户端",
      status: client.connected ? (client.confirmed ? "finish" : "process") : "wait",
    },
    {
      key: "model",
      title: "AI 模型",
      content: model.configured ? "模型已配置" : "未配置，前往 Agent → LLM",
      status: model.configured ? "finish" : "wait",
    },
  ];
}
