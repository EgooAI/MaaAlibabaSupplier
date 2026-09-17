import type { ConnectionSnapshot } from "@/types/connection";

type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
type StorageSource = DraftStorage | (() => DraftStorage);
const drafts = new Map<string, { value: string; dirty: boolean }>();

function persist(storage: StorageSource, key: string, value: string) {
  const target = typeof storage === "function" ? storage() : storage;
  if (value) target.setItem(key, value);
  else target.removeItem(key);
  drafts.set(key, { value, dirty: false });
}

export function draftKey(account: ConnectionSnapshot["account"], conversation: string) {
  return `maa:draft:${JSON.stringify([account.data_dir, account.self_ali_id, conversation])}`;
}

export function loadDraft(storage: StorageSource, account: ConnectionSnapshot["account"], conversation: string, onError?: (error: unknown) => void) {
  const key = draftKey(account, conversation);
  const cached = drafts.get(key);
  try {
    // Unsaved edits, including a deletion, take priority over older storage values.
    if (cached?.dirty) {
      persist(storage, key, cached.value);
      return cached.value;
    }
    const target = typeof storage === "function" ? storage() : storage;
    const value = target.getItem(key) ?? "";
    drafts.set(key, { value, dirty: false });
    return value;
  } catch (error) {
    onError?.(error);
    return cached?.value ?? "";
  }
}

export function saveDraft(storage: StorageSource, account: ConnectionSnapshot["account"], conversation: string, value: string) {
  const key = draftKey(account, conversation);
  drafts.set(key, { value, dirty: true });
  persist(storage, key, value);
}

export function flushDrafts(storage: StorageSource) {
  for (const [key, draft] of drafts) {
    if (!draft.dirty) continue;
    try { persist(storage, key, draft.value); }
    catch { /* Retain unsaved edits for the next observation or workspace mount. */ }
  }
}
