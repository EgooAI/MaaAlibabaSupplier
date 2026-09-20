// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { WorkerStatus } from "@/features/status/WorkerStatus";
import type { WorkerObservation } from "@/types/status";

vi.mock("antd", () => ({
  Card: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Space: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Typography: {
    Text: ({ children }: { children: ReactNode }) => <span>{children}</span>,
    Paragraph: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  },
  Alert: ({ title }: { title: ReactNode }) => <div>{title}</div>,
}));

it("distinguishes a live blocked worker, stopped worker and unstarted queue without claiming readiness", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const container = document.createElement("div");
  const root = createRoot(container);
  const worker: WorkerObservation = {
    started: true, alive: true, stopping: false, started_at: 100, heartbeat_at: 120,
    last_progress_at: 110, observed_at: 180, phase: "reading_source", phase_started_at: 120,
    phase_age_s: 60, completed_iterations: 2, pending: 3, pending_observed_at: 120,
    context: { epoch: "old", self_ali_id: "seller-a", data_dir: "test" }, last_error: null, progress_unit: "verification_iterations",
  };
  try {
    await act(async () => root.render(<WorkerStatus workers={{ "outbox-verifier": worker, "im-source-check": null }} queues={{ translation: { initialized: false, alive: false, pending: 0, current_started: null, current_age_s: null, last_completed: null, observed_at: 180 } }} />));
    expect(container.textContent).toContain("发送结果核对服务：线程存活");
    expect(container.textContent).toContain("持续 60 秒");
    expect(container.textContent).toContain("已完成循环 2");
    expect(container.textContent).toContain("待核对数量：3");
    expect(container.textContent).toContain("卖家 seller-a");
    expect(container.textContent).toContain("翻译任务队列：未启动");
    expect(container.textContent).toContain("不代表源库新鲜、CRM 已提交或消息已送达");
    await act(async () => root.render(<WorkerStatus workers={{ "outbox-verifier": { ...worker, alive: false, phase: "stopped", last_error: "OSError" } }} />));
    expect(container.textContent).toContain("发送结果核对服务：线程未存活");
    expect(container.textContent).toContain("上次循环异常：OSError");
  } finally {
    await act(async () => root.unmount());
  }
});
