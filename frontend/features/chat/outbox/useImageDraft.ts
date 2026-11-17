"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAccount, useAccountBackend, AccountChangedError } from "@/features/account/AccountProvider";
import { captureAccount } from "@/services/accountSession";
import { ApiError } from "@/services/httpAdapter";
import { intentKey } from "./intentStorage";
import { loadImageDraft, saveImageDraft, MAX_IMAGE_BYTES, type ImageDraft } from "./imageDraftStorage";

export function useImageDraft(sid: string) {
  const { snapshot } = useAccount();
  const backend = useAccountBackend();
  const key = `${intentKey(snapshot!.account, sid)}:image`;
  const [draft, setDraft] = useState<ImageDraft>();
  const current = useRef<ImageDraft | undefined>(undefined);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string>();
  const [preview, setPreview] = useState<Blob>();
  const [url, setUrl] = useState<string>();
  const active = useRef(true);
  const sequence = useRef(0);

  const upload = useCallback(async (value: ImageDraft) => {
    const generation = ++sequence.current;
    const valid = () => active.current && sequence.current === generation && current.current?.id === value.id;
    setBusy(true); setError(undefined);
    try {
      const ticket = captureAccount();
      let attachment;
      try { attachment = await backend.getImageAttachment(sid, value.id); }
      catch (cause) {
        if (!(cause instanceof ApiError) || ![404, 410].includes(cause.status ?? 0)) throw cause;
        ticket.assertCurrent();
        if (!valid()) return;
        attachment = await backend.uploadImageAttachment(sid, value.id, value.file);
      }
      ticket.assertCurrent();
      if (!valid()) return;
      if (attachment.id !== value.id || (value.attachment && value.attachment.sha256 !== attachment.sha256)) throw new Error("图片上传回执与草稿不匹配");
      const next = { ...value, attachment };
      if (!await saveImageDraft(key, next, value.id)) throw new Error("图片草稿已在其他页面变化，请重新选择");
      ticket.assertCurrent();
      if (!valid()) return;
      const blob = await backend.getImageAttachmentPreview(sid, value.id);
      ticket.assertCurrent();
      if (!valid()) return;
      current.current = next; setDraft(next); setPreview(blob);
    } catch (cause) {
      if (valid() && !(cause instanceof AccountChangedError)) setError(cause instanceof Error ? cause.message : "图片上传失败，可重试上传");
    } finally { if (active.current && sequence.current === generation) setBusy(false); }
  }, [backend, sid, key]);

  useEffect(() => {
    active.current = true;
    const counter = sequence;
    const generation = sequence.current;
    loadImageDraft(key).then((saved) => {
      if (!active.current || sequence.current !== generation) return;
      if (!saved) { setBusy(false); return; }
      current.current = saved; setDraft(saved); void upload(saved);
    }).catch(() => { if (active.current) { setError("图片草稿读取失败，请重新选择图片"); setBusy(false); } });
    return () => { active.current = false; ++counter.current; };
  }, [key, upload]);

  useEffect(() => {
    if (!preview) return;
    const next = URL.createObjectURL(preview);
    let valid = true;
    queueMicrotask(() => { if (valid) setUrl(next); });
    return () => { valid = false; URL.revokeObjectURL(next); };
  }, [preview]);

  async function select(files: File[]) {
    if (files.length !== 1) { setError("每次只能添加一张图片"); return; }
    const file = files[0];
    if (!file.size || file.size > MAX_IMAGE_BYTES) { setError("图片文件不能超过 10 MB（10,000,000 字节）"); return; }
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) { setError("仅支持静态 PNG、JPEG、WebP 图片"); return; }
    const generation = ++sequence.current;
    const value: ImageDraft = { id: crypto.randomUUID().replaceAll("-", ""), file, name: file.name || "粘贴图片" };
    setBusy(true); setError(undefined);
    try {
      const ticket = captureAccount();
      await saveImageDraft(key, value);
      ticket.assertCurrent();
      if (!active.current || sequence.current !== generation) return;
      current.current = value; setDraft(value); setPreview(undefined); setUrl(undefined);
      await upload(value);
    } catch (cause) {
      if (active.current && sequence.current === generation) {
        if (!(cause instanceof AccountChangedError)) setError(cause instanceof Error ? cause.message : "图片草稿保存失败");
        setBusy(false);
      }
    }
  }

  async function clear(id = current.current?.id) {
    if (!id) return;
    const generation = sequence.current;
    try {
      await saveImageDraft(key, undefined, id);
      if (active.current && sequence.current === generation && current.current?.id === id) {
        ++sequence.current; current.current = undefined; setDraft(undefined); setPreview(undefined); setUrl(undefined); setBusy(false); setError(undefined);
      }
    } catch { if (active.current) setError("图片草稿清理失败，原提交身份仍保留"); }
  }

  return { draft, busy, error, url, select, clear, retry: () => current.current && upload(current.current), ready: Boolean(draft?.attachment && url && !busy && !error) };
}
