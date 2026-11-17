import type { ImageAttachment } from "@/types/chatOperations";

export const MAX_IMAGE_BYTES = 10_000_000;
export type ImageDraft = { id: string; file: Blob; name: string; attachment?: ImageAttachment };

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") { reject(new Error("浏览器不支持图片草稿存储")); return; }
    const request = indexedDB.open("maa-image-drafts", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("drafts");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("图片草稿存储不可用"));
  });
}

export async function loadImageDraft(key: string): Promise<ImageDraft | undefined> {
  if (typeof indexedDB === "undefined") return undefined;
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", "readonly");
    const read = tx.objectStore("drafts").get(key);
    tx.oncomplete = () => { db.close(); resolve(read.result); };
    tx.onabort = () => { db.close(); reject(new Error("图片草稿读取失败")); };
  });
}

/** Writes or removes only the expected draft, so late upload receipts cannot replace a new image. */
export async function saveImageDraft(key: string, value: ImageDraft | undefined, expectedId?: string): Promise<boolean> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", "readwrite");
    const store = tx.objectStore("drafts");
    const read = store.get(key);
    let changed = false;
    read.onsuccess = () => {
      if (expectedId && read.result?.id !== expectedId) return;
      if (value) store.put(value, key); else store.delete(key);
      changed = true;
    };
    tx.oncomplete = () => { db.close(); resolve(changed); };
    tx.onabort = () => { db.close(); reject(new Error("图片草稿保存失败，已暂停图片提交")); };
  });
}
