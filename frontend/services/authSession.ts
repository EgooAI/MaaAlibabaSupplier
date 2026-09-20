import { accountSession } from "./accountSession";

export const AUTH_STORAGE_KEY = "maa:auth:v1";
type Phase = "loading" | "signedOut" | "verifying" | "retry" | "persist" | "authenticated";
type AuthState = { phase: Phase; generation: number; error?: string; warning?: string; retryAt: number; temporary: boolean };
const initial: AuthState = { phase: "loading", generation: 0, retryAt: 0, temporary: false };
let state = initial;
let token: string | null = null;
let lifetime = new AbortController();
const listeners = new Set<() => void>();

export class AuthChangedError extends Error {
  constructor() {
    super("登录状态已变化，请重新登录后重试");
    this.name = "AuthChangedError";
  }
}

function publish(update: Partial<AuthState>) {
  state = { ...state, ...update };
  listeners.forEach((listener) => listener());
}

function replace(nextToken: string | null, phase: Phase) {
  const previous = lifetime;
  lifetime = new AbortController();
  token = nextToken;
  state = { ...state, phase, generation: state.generation + 1, error: undefined, warning: undefined, temporary: false };
  previous.abort();
  accountSession.clear();
  listeners.forEach((listener) => listener());
  return state.generation;
}

function storageLocks() {
  const locks = globalThis.navigator?.locks;
  if (!locks) throw new Error("浏览器不支持 Web Locks，无法安全使用共享登录存储；可选择仅在本页临时登录。");
  return locks;
}

async function removeStoredToken(previous: string | null, generation: number) {
  if (!previous) return;
  try {
    // Writers and conditional removals must share this origin-wide lock.
    await storageLocks().request(AUTH_STORAGE_KEY, () => {
      if (generation !== state.generation) return;
      if (localStorage.getItem(AUTH_STORAGE_KEY) === previous) localStorage.removeItem(AUTH_STORAGE_KEY);
    });
  } catch {
    if (generation !== state.generation) return;
    publish({ warning: [state.warning, "无法安全清除浏览器中的登录凭证。已停止本页请求，请在浏览器设置中清除此站点的登录凭证。"].filter(Boolean).join(" ") });
  }
}

export class AuthRequestError extends Error {
  readonly requestId?: string;

  constructor(message: string, readonly status?: number, readonly retryAt = 0, requestId?: string) {
    const reference = requestId && requestId.length <= 128 && !/[^A-Za-z0-9._:-]/.test(requestId) ? requestId : undefined;
    super(reference ? `${message}（请求 ID：${reference}）` : message);
    this.name = "AuthRequestError";
    this.requestId = reference;
  }
}

async function authRequest(path: string, currentToken: string | null, signal: AbortSignal, body?: unknown) {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (currentToken) headers.set("Authorization", `Bearer ${currentToken}`);
  const response = await fetch(`/api/auth/${path}`, {
    method: path === "session" ? "GET" : "POST", headers, cache: "no-store", credentials: "omit",
    signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const requestId = response.headers.get("X-Request-ID") ?? undefined;
  // Status takes precedence even when a proxy returns an empty/non-JSON body.
  if (response.status === 401) throw new AuthRequestError("登录凭证无效，请重新登录", 401, 0, requestId);
  if (response.status === 429) {
    const retry = response.headers.get("Retry-After");
    const seconds = retry === null ? NaN : Number(retry);
    const retryAt = Number.isFinite(seconds) ? Date.now() + seconds * 1000 : Date.parse(retry ?? "");
    throw new AuthRequestError("登录服务请求过于频繁，请稍后重试", 429, Math.max(Date.now() + 2000, retryAt || 0), requestId);
  }
  if (!response.ok) throw new AuthRequestError("登录服务暂不可用，请重试", response.status, 0, requestId);
  let envelope;
  try {
    envelope = await response.json();
  } catch {
    throw new AuthRequestError("登录服务响应读取失败，请重试", response.status, 0, requestId);
  }
  if (envelope?.code !== 0 || typeof envelope.msg !== "string" || !("data" in envelope)) {
    throw new AuthRequestError("登录服务返回了无效响应，请重试", response.status, 0, requestId);
  }
  if (path === "session" && envelope.data?.authenticated !== true) throw new AuthRequestError("登录服务返回了无效会话，请重试", response.status, 0, requestId);
  if (path === "login" && (typeof envelope.data?.token !== "string" || !envelope.data.token)) throw new AuthRequestError("登录服务返回了无效凭证，请重试", response.status, 0, requestId);
  return envelope.data;
}

async function verify() {
  if (!token || Date.now() < state.retryAt) return;
  const currentToken = token;
  const generation = replace(currentToken, "verifying");
  try {
    await authRequest("session", currentToken, lifetime.signal);
    if (generation !== state.generation) return;
    publish({ phase: "authenticated" });
  } catch (error) {
    if (generation !== state.generation) return;
    if (error instanceof AuthRequestError && error.status === 401) {
      await authSession.unauthorized(generation, error.requestId);
    } else {
      publish({ phase: "retry", error: error instanceof Error ? error.message : "无法验证登录，请重试", retryAt: error instanceof AuthRequestError ? error.retryAt : 0 });
    }
  }
}

async function persist() {
  if (!token || state.phase !== "persist") return;
  const generation = state.generation;
  const currentToken = token;
  try {
    await storageLocks().request(AUTH_STORAGE_KEY, () => {
      if (generation !== state.generation || state.phase !== "persist") return;
      localStorage.setItem(AUTH_STORAGE_KEY, currentToken);
      if (localStorage.getItem(AUTH_STORAGE_KEY) !== currentToken) throw new Error("登录凭证保存校验失败");
      publish({ phase: "authenticated", error: undefined });
    });
  } catch (error) {
    if (generation !== state.generation || state.phase !== "persist") return;
    publish({ error: storageAvailableMessage(error, "无法保存登录凭证。可重试保存，或明确选择仅在本页临时登录；刷新或关闭页面后需要重新登录。") });
  }
}

function storageAvailableMessage(error: unknown, fallback: string) {
  return !globalThis.navigator?.locks && error instanceof Error ? error.message : fallback;
}

export const authSession = {
  get: () => state,
  server: () => initial,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  async bootstrap() {
    let stored: string | null;
    try {
      storageLocks();
      stored = localStorage.getItem(AUTH_STORAGE_KEY);
    } catch (error) {
      replace(null, "signedOut");
      publish({ warning: storageAvailableMessage(error, "无法读取浏览器登录凭证。登录后可选择仅在本页临时使用。") });
      return;
    }
    replace(stored || null, stored ? "retry" : "signedOut");
    if (stored) await verify();
  },
  async login(secret: string) {
    if (state.phase !== "signedOut" || Date.now() < state.retryAt) return;
    const generation = replace(null, "verifying");
    try {
      if (!globalThis.isSecureContext || !globalThis.crypto?.subtle) {
        throw new Error("登录需要安全连接，请使用 HTTPS 或本机 localhost 地址。");
      }
      const bytes = new TextEncoder().encode(secret);
      secret = "";
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      bytes.fill(0);
      if (generation !== state.generation) return;
      const secret_sha256 = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
      const data = await authRequest("login", null, lifetime.signal, { secret_sha256 });
      if (generation !== state.generation) return;
      token = data.token;
      publish({ phase: "persist" });
      await persist();
    } catch (error) {
      if (generation !== state.generation) return;
      publish({ phase: "signedOut", error: error instanceof Error ? error.message : "登录失败，请重试", retryAt: error instanceof AuthRequestError ? error.retryAt : 0 });
    }
  },
  retry: verify,
  persist,
  useTemporarySession() {
    if (state.phase === "persist" && token) publish({ phase: "authenticated", temporary: true, error: undefined });
  },
  async unauthorized(generation: number, requestId?: string) {
    if (generation !== state.generation) return;
    const previous = token;
    const signedOutGeneration = replace(null, "signedOut");
    publish({ error: new AuthRequestError("登录凭证已失效，请重新登录", 401, 0, requestId).message });
    await removeStoredToken(previous, signedOutGeneration);
  },
  async logout() {
    const previous = token;
    const generation = replace(null, "signedOut");
    const removal = removeStoredToken(previous, generation);
    try {
      if (previous) await authRequest("logout", previous, new AbortController().signal);
    } catch (error) {
      if (generation !== state.generation || (error instanceof AuthRequestError && error.status === 401)) return;
      const warning = new AuthRequestError("本页已退出，但服务端撤销凭证失败；该凭证可能仍有效。", undefined, 0, error instanceof AuthRequestError ? error.requestId : undefined).message;
      publish({ warning: [state.warning, warning].filter(Boolean).join(" ") });
    } finally {
      await removal;
    }
  },
  async onStorage(event: StorageEvent) {
    if (event.key !== AUTH_STORAGE_KEY && event.key !== null) return;
    try {
      if (event.storageArea && event.storageArea !== localStorage) return;
      storageLocks();
      const stored = localStorage.getItem(AUTH_STORAGE_KEY);
      if (stored && stored === token && event.key !== null) return;
      replace(stored || null, stored ? "retry" : "signedOut");
      if (stored) await verify();
    } catch (error) {
      replace(null, "signedOut");
      publish({ warning: storageAvailableMessage(error, "无法读取其他标签页的登录变更，请重新登录。") });
    }
  },
};

export function captureAuth() {
  if (state.phase !== "authenticated" || !token) throw new AuthChangedError();
  const generation = state.generation;
  return {
    token, generation, signal: lifetime.signal,
    assertCurrent() {
      if (generation !== state.generation || state.phase !== "authenticated") throw new AuthChangedError();
    },
  };
}
