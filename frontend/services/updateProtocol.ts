import type { UpdateState } from "@/types/update";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

export function parseUpdateState(value: unknown): UpdateState {
  if (!isRecord(value)
    || typeof value.supported !== "boolean"
    || !isNullableString(value.reason)
    || typeof value.phase !== "string"
    || !["idle", "checking", "available", "downloading", "ready", "installing", "error"].includes(value.phase)
    || !isRecord(value.current)
    || typeof value.current.version !== "string"
    || !isNullableString(value.current.sha)
    || (value.current.run_id !== undefined && value.current.run_id !== null && !isCount(value.current.run_id))
    || !isRecord(value.source)
    || typeof value.source.repository !== "string"
    || typeof value.source.branch !== "string"
    || typeof value.source.workflow !== "string"
    || typeof value.source.artifact !== "string"
    || (value.candidate !== null && (!isRecord(value.candidate)
      || typeof value.candidate.id !== "string"
      || typeof value.candidate.version !== "string"
      || typeof value.candidate.sha !== "string"
      || !isCount(value.candidate.run_id)
      || !isCount(value.candidate.run_attempt)
      || typeof value.candidate.created_at !== "string"
      || typeof value.candidate.url !== "string"))
    || !isCount(value.downloaded_bytes)
    || (value.total_bytes !== null && !isCount(value.total_bytes))
    || !isNullableString(value.error)
    || (value.last_result !== null && (!isRecord(value.last_result)
      || typeof value.last_result.status !== "string"
      || typeof value.last_result.message !== "string"
      || !isNullableString(value.last_result.version)))) {
    throw new Error("更新状态响应无效，请刷新本地状态重试");
  }
  return value as unknown as UpdateState;
}

export function parseInstallAcceptance(value: unknown): { accepted: true } {
  if (!isRecord(value) || value.accepted !== true) throw new Error("安装请求未返回有效确认");
  return { accepted: true };
}
