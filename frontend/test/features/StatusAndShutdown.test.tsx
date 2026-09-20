// @vitest-environment happy-dom
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useStatusWorkbench } from "@/features/status/hooks/useStatusWorkbench";
import { useShutdownApp } from "@/features/settings/hooks/useShutdownApp";
import { ApiError } from "@/services/httpAdapter";
import { accountSession } from "@/services/accountSession";
import type { SystemStatusSnapshot } from "@/types/status";

const mocks = vi.hoisted(() => ({ backend: { getSystemStatus: vi.fn(), shutdownApp: vi.fn() }, message: { error: vi.fn() } }));
vi.mock("@/services/client", () => ({ backend: mocks.backend }));
vi.mock("antd", () => ({ App: { useApp: () => ({ message: mocks.message }) } }));
let root: Root;
let container: HTMLDivElement;
let status: ReturnType<typeof useStatusWorkbench>;
let shutdown: ReturnType<typeof useShutdownApp>;
function StatusHarness() {
  const current = useStatusWorkbench();
  useEffect(() => { status = current; }, [current]);
  return <div>{current.lastFailure?.message}</div>;
}
function ShutdownHarness() {
  const current = useShutdownApp();
  useEffect(() => { shutdown = current; }, [current]);
  return <div>{current.shutdownError}</div>;
}
const snapshot: SystemStatusSnapshot = { updatedAt: "last success", modules: [], tasks: [] };

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("serializes slow observations and preserves the last snapshot with a visible failure", async () => {
  let resolve!: (snapshot: SystemStatusSnapshot) => void;
  mocks.backend.getSystemStatus.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  await act(async () => root.render(<StatusHarness />));
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(mocks.backend.getSystemStatus).toHaveBeenCalledTimes(1);
  await act(async () => resolve(snapshot));
  expect(status.snapshot).toEqual(snapshot);
  mocks.backend.getSystemStatus.mockRejectedValueOnce(new ApiError("network lost", "/api/status", { kind: "transport", requestId: "poll-2" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(status.snapshot).toEqual(snapshot);
  expect(status.lastFailure?.at).toBe(Date.now());
  expect(container.textContent).toContain("poll-2");
  mocks.backend.getSystemStatus.mockResolvedValueOnce({ ...snapshot, updatedAt: "recovered" });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(status.snapshot?.updatedAt).toBe("recovered");
  expect(status.lastFailure).toBeUndefined();
});

it("discards a status response from an earlier account selection", async () => {
  let resolve!: (snapshot: SystemStatusSnapshot) => void;
  mocks.backend.getSystemStatus.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  await act(async () => root.render(<StatusHarness />));
  await act(async () => { accountSession.invalidate(); resolve(snapshot); });
  expect(status.snapshot).toBeUndefined();
});

it.each(["accepted", "rejected", "lost"])("reports shutdown %s without claiming process exit or closing the tab", async (outcome) => {
  const close = vi.spyOn(window, "close").mockImplementation(() => {});
  if (outcome === "lost") mocks.backend.shutdownApp.mockRejectedValue(new ApiError("connection lost", "/api/app/shutdown", { kind: "transport", requestId: "stop-1" }));
  else mocks.backend.shutdownApp.mockResolvedValue({ accepted: outcome === "accepted" });
  await act(async () => root.render(<ShutdownHarness />));
  await act(async () => shutdown.confirmShutdown());
  expect(shutdown.terminated).toBe(outcome === "accepted");
  if (outcome === "rejected") expect(container.textContent).toContain("未接受");
  if (outcome === "lost") expect(container.textContent).toContain("结果未知");
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(close).not.toHaveBeenCalled();
  expect(mocks.backend.shutdownApp).toHaveBeenCalledTimes(1);
});
