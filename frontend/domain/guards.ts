/** 共享类型守卫单源，避免 httpAdapter/cardModel 各写一次 isPlainObject。 */
export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

/** trim 判空版：非空字符串才返回值，否则 undefined。用于展示层空值归一。 */
export function stringField(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}
