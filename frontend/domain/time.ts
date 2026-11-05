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

/** 固定格式 YYYY-MM-DD HH:mm:ss，用于悬浮提示和需要精确到秒的场合；非法输入同 formatDateTime。 */
export function formatFullDateTime(value: string | number | null | undefined): string {
  if (typeof value === "string" && !value.trim()) return "未知时间";
  const date = toDate(value);
  if (!date) return typeof value === "string" ? value : "未知时间";
  return `${formatDate(date)}:${pad(date.getSeconds())}`;
}

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

function dayNumber(date: Date) {
  return Math.round(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86_400_000);
}

function mondayOf(date: Date) {
  return dayNumber(date) - ((date.getDay() + 6) % 7);
}

/**
 * 聊天工作台统一的时间文案，按下列顺序取第一个命中的规则：
 * 刚刚/即将、X分钟前/后、今天、昨天/明天、星期X、MM-DD、YYYY-MM-DD。
 * “今天”等按本地日历日判断，一周从周一开始。`now` 由调用方传入，便于测试固定时钟。
 */
export function formatSmartTime(value: string | number | null | undefined, now: Date = new Date()): string {
  const date = toDate(value);
  if (!date) return typeof value === "string" && value.trim() ? value : "未知时间";
  const delta = date.getTime() - now.getTime();
  const seconds = Math.abs(delta) / 1000;
  if (seconds < 60) return delta > 0 ? "即将" : "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟${delta > 0 ? "后" : "前"}`;
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const days = dayNumber(date) - dayNumber(now);
  if (days === 0) return `今天 ${clock}`;
  if (days === -1) return `昨天 ${clock}`;
  if (days === 1) return `明天 ${clock}`;
  if (mondayOf(date) === mondayOf(now)) return `星期${WEEKDAYS[date.getDay()]} ${clock}`;
  if (date.getFullYear() === now.getFullYear()) return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${clock}`;
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
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
