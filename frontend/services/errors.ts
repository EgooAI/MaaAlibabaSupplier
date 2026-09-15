/** 后端错误统一取文案，避免各处复制 `instanceof Error` 样板。逻辑对等：仅取 message，不吞栈。 */
export function backendErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export const BUSY_TEXT = "操作进行中，请稍候";
