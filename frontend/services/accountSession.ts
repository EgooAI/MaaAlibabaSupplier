import type { ConnectionSnapshot } from "@/types/connection";
import type { OperationsBackend } from "./interfaces";

export class AccountChangedError extends Error {
  constructor() {
    super("账号状态已变化，请等待刷新后重试");
    this.name = "AccountChangedError";
  }
}

const initial = { snapshot: null as ConnectionSnapshot | null, generation: 0, blocked: true, suspended: false };
let state = initial;
const listeners = new Set<() => void>();
export const accountSession = {
  get: () => state,
  server: () => initial,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  clear() {
    state = { ...initial, generation: state.generation + 1 };
    listeners.forEach((listener) => listener());
  },
  invalidate() {
    state = { ...state, generation: state.generation + 1, blocked: true, suspended: false };
    listeners.forEach((listener) => listener());
  },
  suspend() {
    // An invalidated settings/account scope must never become resumable.
    state = { ...state, blocked: true, suspended: state.suspended || !state.blocked };
    listeners.forEach((listener) => listener());
  },
  accept(snapshot: ConnectionSnapshot) {
    const previous = state.snapshot?.account;
    const changed = previous?.epoch !== snapshot.account.epoch || previous?.data_dir !== snapshot.account.data_dir || previous?.self_ali_id !== snapshot.account.self_ali_id;
    state = { snapshot, generation: state.generation + (changed ? 1 : 0), blocked: false, suspended: false };
    listeners.forEach((listener) => listener());
  },
};

export function captureAccount(allowSuspended = false) {
  const captured = accountSession.get();
  if ((captured.blocked && !(allowSuspended && captured.suspended)) || !captured.snapshot?.account.epoch) throw new AccountChangedError();
  const epoch = captured.snapshot.account.epoch;
  return {
    epoch,
    assertCurrent(serverEpoch?: string | null) {
      const current = accountSession.get();
      if (current.blocked || current.generation !== captured.generation || current.snapshot?.account.epoch !== epoch || (serverEpoch != null && serverEpoch !== epoch)) {
        throw new AccountChangedError();
      }
    },
  };
}

// Bind the entire async chain, including requests started after an await, to its workspace.
export function scopeBackend(backend: OperationsBackend, isActive: () => boolean = () => true): OperationsBackend {
  const ticket = captureAccount(true);
  return new Proxy(backend, {
    get(target, key: keyof OperationsBackend) {
      return async (...args: unknown[]) => {
        const assert = () => {
          if (!isActive()) throw new AccountChangedError();
          ticket.assertCurrent();
        };
        assert();
        try {
          return await (target[key] as (...values: unknown[]) => Promise<unknown>)(...args);
        } finally {
          assert();
        }
      };
    },
  });
}
