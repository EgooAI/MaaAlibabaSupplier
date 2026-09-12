export function nowText(): string {
  return formatDateTime(Date.now() / 1000);
}

export function formatDateTime(value: string | number | null | undefined): string {
  if (typeof value === "string") return value;
  if (typeof value !== "number" || !Number.isFinite(value)) return "未知时间";
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false }).replaceAll("/", "-");
}
