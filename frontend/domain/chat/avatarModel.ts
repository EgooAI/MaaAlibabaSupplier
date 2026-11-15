const CUSTOMER_AVATAR_COLORS = [
  "#0e9f6e",
  "#1c64f2",
  "#7e3af2",
  "#c81e1e",
  "#b45309",
  "#0694a2",
  "#e74694",
  "#4658ac",
];

export function avatarColorOf(id: string) {
  const seed = Array.from(id ?? "").reduce((total, character) => total + character.charCodeAt(0), 0);
  return CUSTOMER_AVATAR_COLORS[seed % CUSTOMER_AVATAR_COLORS.length];
}

export function avatarInitialOf(name?: string | null) {
  const text = (name ?? "").trim();
  if (!text) return "?";
  const first = Array.from(text)[0];
  return /[a-z]/.test(first) ? first.toUpperCase() : first;
}

/** Resolves platform avatar URLs without treating missing or relative paths as local resources. */
export function avatarUrlOf(value?: string | null): string | undefined {
  if (!value?.trim()) return undefined;
  const source = value.trim();
  try {
    const url = new URL(source.startsWith("//") ? `https:${source}` : source);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return undefined;
    return url.href;
  } catch {
    return undefined;
  }
}
