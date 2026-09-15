function pad(value: number) {
  return String(value).padStart(2, "0");
}

function toDate(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return new Date(value * 1000);
  }
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const timestamp = Date.parse(normalized);
  return Number.isNaN(timestamp) ? null : new Date(timestamp);
}

function formatDate(date: Date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 固定格式 YYYY-MM-DD HH:mm；非法输入返回原文或“未知时间”，不依赖运行 locale。 */
export function formatDateTime(value: string | number | null | undefined): string {
  if (typeof value === "string" && !value.trim()) return "未知时间";
  const date = toDate(value);
  if (!date) return typeof value === "string" ? value : "未知时间";
  return formatDate(date);
}

/** 列表用短格式 MM-DD HH:mm；解析失败返回原文。 */
export function formatMonthDay(value: string | number | null | undefined): string {
  const date = toDate(value);
  if (!date) return typeof value === "string" ? value : "未知时间";
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function nowText(): string {
  return formatDateTime(Date.now() / 1000);
}
