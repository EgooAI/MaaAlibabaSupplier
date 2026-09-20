import { ApiError } from "./httpAdapter";

/** 后端错误统一取文案，避免各处复制 `instanceof Error` 样板。逻辑对等：仅取 message，不吞栈。 */
export function backendErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export const BUSY_TEXT = "操作进行中，请稍候";

export function operationErrorMessage(error: unknown, fallback: string, reconciliation = "请先刷新查看结果，勿立即重复提交"): string {
  const unknown = error instanceof ApiError && (error.kind !== "http" || (error.status ?? 0) >= 500);
  const text = unknown ? `操作结果未知，${reconciliation}。${error.message}` : backendErrorMessage(error, fallback);
  return error instanceof ApiError && error.requestId ? `${text}（请求 ID：${error.requestId}）` : text;
}
