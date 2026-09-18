import { authSession, AUTH_STORAGE_KEY } from "@/services/authSession";
import { vi } from "vitest";

export function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
    clear: () => values.clear(),
  };
}

// Exclusive, named queues shared by simulated tabs; callbacks hold the lock
// until their returned promise settles, as with navigator.locks.request.
export function memoryLocks() {
  const queues = new Map<string, Promise<unknown>>();
  return {
    request: vi.fn(<T>(name: string, callback: () => T | PromiseLike<T>): Promise<T> => {
      const result = (queues.get(name) ?? Promise.resolve()).then(callback);
      queues.set(name, result.catch(() => {}));
      return result;
    }),
  };
}

// Exercise the real bootstrap contract; no production authentication bypass.
export async function authenticatedSession(token = "test-session") {
  if (typeof localStorage === "undefined") vi.stubGlobal("localStorage", memoryStorage());
  if (!globalThis.navigator?.locks) vi.stubGlobal("navigator", { locks: memoryLocks() });
  await navigator.locks.request(AUTH_STORAGE_KEY, () => localStorage.setItem(AUTH_STORAGE_KEY, token));
  const previousFetch = globalThis.fetch;
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 0, msg: "ok", data: { authenticated: true } })));
  try { await authSession.bootstrap(); }
  finally { globalThis.fetch = previousFetch; }
  if (authSession.get().phase !== "authenticated") throw new Error("Auth fixture did not establish a verified session");
}
