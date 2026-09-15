"use client";

import { useState } from "react";
import { backend } from "@/services/client";

export function useShutdownApp() {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [terminating, setTerminating] = useState(false);
  const [terminated, setTerminated] = useState(false);

  const confirmShutdown = async () => {
    setTerminating(true);
    try {
      // The backend responds before shutting down; if the connection drops
      // first (response lost to shutdown), the program is still exiting.
      await backend.shutdownApp();
    } catch {
      // ignore — treat as shutdown in progress
    }
    setTerminated(true);
    setConfirmOpen(false);
    setTerminating(false);
    window.setTimeout(() => window.close(), 3000);
  };

  return {
    confirmOpen,
    setConfirmOpen,
    terminating,
    terminated,
    confirmShutdown,
  };
}
