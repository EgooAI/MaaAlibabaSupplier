import { beforeEach, describe, expect, it, vi } from "vitest";
import { accountSession, captureAccount, scopeBackend } from "@/services/accountSession";
import { connectionSnapshot } from "@/mock/connectionData";
import type { OperationsBackend } from "@/services/interfaces";

beforeEach(() => accountSession.accept(structuredClone(connectionSnapshot)));

describe("account lifetime", () => {
  it("invalidates old chains even if A -> B -> A returns the same epoch", () => {
    const ticket = captureAccount();
    accountSession.accept({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "b", self_ali_id: "b" } });
    accountSession.accept(structuredClone(connectionSnapshot));
    expect(() => ticket.assertCurrent()).toThrow("账号状态已变化");
  });

  it("keeps chains valid for ordinary observations and revokes them before a mutation", () => {
    const ticket = captureAccount();
    accountSession.accept(structuredClone(connectionSnapshot));
    expect(() => ticket.assertCurrent()).not.toThrow();
    accountSession.invalidate();
    accountSession.accept(structuredClone(connectionSnapshot));
    expect(() => ticket.assertCurrent()).toThrow("账号状态已变化");
  });

  it("stops a chain from starting its next request with the new account token", async () => {
    const nextRequest = vi.fn();
    const source = { getSelfInfo: vi.fn().mockResolvedValue(null), listConversations: nextRequest } as unknown as OperationsBackend;
    const scoped = scopeBackend(source);
    await scoped.getSelfInfo();
    accountSession.invalidate();
    accountSession.accept({ ...connectionSnapshot, account: { ...connectionSnapshot.account, epoch: "b" } });
    await expect(scoped.listConversations()).rejects.toThrow("账号状态已变化");
    expect(nextRequest).not.toHaveBeenCalled();
  });

  it("drops an in-flight result when its workspace unmounts", async () => {
    let active = true;
    let resolve!: (value: null) => void;
    const source = { getSelfInfo: () => new Promise<null>((done) => { resolve = done; }) } as OperationsBackend;
    const scoped = scopeBackend(source, () => active);
    const result = scoped.getSelfInfo();
    active = false;
    resolve(null);
    await expect(result).rejects.toThrow("账号状态已变化");
  });

  it("discards a failed request from an obsolete workspace as well", async () => {
    let reject!: (error: Error) => void;
    const source = { getSelfInfo: () => new Promise<null>((_, fail) => { reject = fail; }) } as OperationsBackend;
    const scoped = scopeBackend(source);
    const result = scoped.getSelfInfo();
    accountSession.invalidate();
    reject(new Error("network failed"));
    await expect(result).rejects.toThrow("账号状态已变化");
  });
});
