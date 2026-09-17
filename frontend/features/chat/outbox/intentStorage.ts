import type { ConnectionSnapshot } from "@/types/connection";

export type PendingIntent = { key: string; content: string; action: "send" | "test"; taskId?: string };

export function intentKey(account: ConnectionSnapshot["account"], sid: string) {
  return `maa:outbox-intents:${JSON.stringify([account.data_dir, account.self_ali_id, sid])}`;
}

export function loadIntents(storage: Storage, key: string): PendingIntent[] {
  const raw = storage.getItem(key);
  if (raw === null) return [];
  const items: unknown = JSON.parse(raw);
  if (!Array.isArray(items) || items.some((item) => !item || typeof item.key !== "string" || !item.key || typeof item.content !== "string" || !["send", "test"].includes(item.action) || (item.taskId !== undefined && (typeof item.taskId !== "string" || !item.taskId)))) {
    throw new Error("提交记录损坏");
  }
  return items;
}

// Persist before any network mutation. Never fall back to an unpersisted key.
export function persistIntents(storage: Storage, key: string, incoming: PendingIntent[]) {
  const items = loadIntents(storage, key);
  for (const intent of incoming) {
    const index = items.findIndex((item) => item.key === intent.key);
    if (index < 0) items.push(intent);
    else {
      const old = items[index];
      if (old.content !== intent.content || old.action !== intent.action || (old.taskId && intent.taskId && old.taskId !== intent.taskId)) throw new Error("提交记录不匹配");
      items[index] = { ...old, ...intent };
    }
  }
  const encoded = JSON.stringify(items);
  if (storage.getItem(key) !== encoded) storage.setItem(key, encoded);
  if (storage.getItem(key) !== encoded) throw new Error("提交记录无法持久化");
  return items;
}
