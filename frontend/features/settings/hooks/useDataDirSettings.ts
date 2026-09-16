"use client";

import { useCallback, useEffect, useState } from "react";
import { backend } from "@/services/client";
import type { DataDirStatus } from "@/types/status";

export function useDataDirSettings() {
  const [status, setStatus] = useState<DataDirStatus | null>(null);
  const [candidates, setCandidates] = useState<string[]>([]);
  const [path, setPath] = useState("");
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await backend.getDataDirStatus();
      setStatus(next);
      setPath(next.path);
    } catch {
      setError("数据目录状态加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    async function loadInitialStatus() {
      await reload();
    }

    void loadInitialStatus();
  }, [reload]);

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
      const next = await backend.saveDataDirPath(trimmed);
      setStatus(next);
      setPath(next.path);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
      return false;
    } finally {
      setSaving(false);
    }
  }, [path]);

  return { status, candidates, path, setPath, loading, scanning, saving, error, reload, scan, save };
}
