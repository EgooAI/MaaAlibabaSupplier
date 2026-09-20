"use client";

import { useState } from "react";
import { backend } from "@/services/client";
import { operationErrorMessage } from "@/services/errors";

export function useShutdownApp() {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [terminating, setTerminating] = useState(false);
  const [terminated, setTerminated] = useState(false);
  const [shutdownError, setShutdownError] = useState<string>();

  const confirmShutdown = async () => {
    if (terminating || terminated) return;
    setTerminating(true);
    setShutdownError(undefined);
    try {
      const receipt = await backend.shutdownApp();
      if (receipt.accepted) setTerminated(true);
      else setShutdownError("后端未接受关闭请求，程序是否退出尚未确认。");
    } catch (error) {
      setShutdownError(operationErrorMessage(error, "关闭结果未知，请检查程序状态", "请检查程序是否仍在运行，勿将断线视为已退出"));
    }
    setConfirmOpen(false);
    setTerminating(false);
  };

  return {
    confirmOpen,
    setConfirmOpen,
    terminating,
    terminated,
    shutdownError,
    confirmShutdown,
  };
}
