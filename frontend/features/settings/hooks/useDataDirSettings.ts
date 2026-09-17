"use client";

import { useCallback, useState } from "react";
import { backend } from "@/services/client";
import { useAccount } from "@/features/account/AccountProvider";

export function useDataDirSettings() {
  const { snapshot, blocked, refresh: reload, mutate, mutating, error: connectionError } = useAccount();
  const status = snapshot?.data_dir ?? null;
  const [candidates, setCandidates] = useState<string[]>([]);
  const [input, setInput] = useState<{ source: string; path: string }>();
  const source = status?.path ?? "";
  const path = input?.source === source ? input.path : source;
  const setPath = useCallback((path: string) => setInput({ source, path }), [source]);
  const loading = !snapshot && !connectionError;
  const [scanning, setScanning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      const result = await backend.listDataDirCandidates();
      setCandidates(result.candidates);
      if (result.candidates.length === 0) setError("未在各盘符根目录发现 AlibabaSupplierData，请手动填写");
    } catch {
      setError("自动探测失败，请手动填写");
    } finally {
      setScanning(false);
    }
  }, []);

  const save = useCallback(async () => {
    const trimmed = path.trim();
    if (!trimmed) {
      setError("请填写数据目录路径");
      return false;
    }
    setSaving(true);
    setError(null);
    try {
      const next = await mutate((epoch) => backend.saveDataDirPath(trimmed, epoch));
      setPath(next.path);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
      return false;
    } finally {
      setSaving(false);
    }
  }, [path, mutate, setPath]);

  return { status, candidates, path, setPath, loading, scanning, saving: saving || mutating, canSave: !blocked && Boolean(snapshot) && !mutating, error: error || connectionError, reload, scan, save };
}
