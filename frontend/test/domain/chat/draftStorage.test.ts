import { describe, expect, it, vi } from "vitest";
import { draftKey, flushDrafts, loadDraft, saveDraft } from "@/features/account/draftStorage";

const account = { data_dir: "D:\\Data", self_ali_id: "seller-a", epoch: "first" };

describe("account drafts", () => {
  it("survives A -> B -> A and reloads while separating directory, seller, and conversation", () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
    saveDraft(storage, account, "42", "draft A");
    const other = { ...account, self_ali_id: "seller-b" };
    expect(loadDraft(storage, other, "42")).toBe("");
    saveDraft(storage, other, "42", "draft B");
    expect(loadDraft(storage, { ...account, epoch: "after-reload" }, "42")).toBe("draft A");
    expect(loadDraft(storage, { ...account, data_dir: "E:\\Data" }, "42")).toBe("");
    expect(loadDraft(storage, account, "43")).toBe("");
    expect(loadDraft(storage, other, "42")).toBe("draft B");
    saveDraft(storage, account, "42", "");
    expect(loadDraft(storage, account, "42")).toBe("");
    expect(loadDraft(storage, other, "42")).toBe("draft B");
  });

  it("does not collide when identifiers contain separators", () => {
    expect(draftKey({ ...account, data_dir: "a:b", self_ali_id: "c" }, "d")).not.toBe(draftKey({ ...account, data_dir: "a", self_ali_id: "b:c" }, "d"));
  });

  it("preserves unsaved drafts even when accessing localStorage itself throws", () => {
    const storage = () => { throw new Error("storage denied"); };
    const warning = vi.fn();
    expect(() => saveDraft(storage, account, "blocked-access", "draft")).toThrow("storage denied");
    expect(loadDraft(storage, account, "blocked-access", warning)).toBe("draft");
    expect(warning).toHaveBeenCalledWith(expect.objectContaining({ message: "storage denied" }));
    expect(loadDraft(storage, { ...account, self_ali_id: "seller-b" }, "blocked-access")).toBe("");
    expect(loadDraft(storage, { ...account, data_dir: "E:\\Data" }, "blocked-access")).toBe("");
  });

  it("keeps failed deletions over stale storage and flushes them when storage recovers", () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
    saveDraft(storage, account, "deleted", "old draft");
    const blocked = { ...storage, removeItem: () => { throw new Error("blocked"); } };
    expect(() => saveDraft(blocked, account, "deleted", "")).toThrow("blocked");
    expect(loadDraft(blocked, account, "deleted")).toBe("");
    expect(storage.getItem(draftKey(account, "deleted"))).toBe("old draft");
    flushDrafts(storage);
    expect(storage.getItem(draftKey(account, "deleted"))).toBeNull();
    expect(loadDraft(storage, account, "deleted")).toBe("");
  });
});
