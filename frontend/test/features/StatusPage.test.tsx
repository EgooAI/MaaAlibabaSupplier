// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkerStatus } from "@/features/status/WorkerStatus";
import { BackgroundTasks } from "@/features/status/BackgroundTasks";
import { StatusPage } from "@/features/status/StatusPage";
import { backgroundStatus, runtimeResource, statusSnapshot, workerObservation } from "@/test/support/backgroundFixture";
import type { useStatusWorkbench } from "@/features/status/hooks/useStatusWorkbench";

const mocks = vi.hoisted(() => ({ workbench: {} as ReturnType<typeof useStatusWorkbench>, blocked: false, connected: true }));
vi.mock("@/features/status/hooks/useStatusWorkbench", () => ({ useStatusWorkbench: () => mocks.workbench }));
vi.mock("@/features/account/AccountProvider", () => ({ useAccount: () => ({ blocked: mocks.blocked, snapshot: { client: { connected: mocks.connected } } }) }));
vi.mock("@/features/account/SyncStatus", () => ({ SyncStatus: () => <button>刷新聊天</button> }));
vi.mock("@/features/settings/DataDirBanner", () => ({ DataDirBanner: () => null }));

let root: Root;
let container: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  mocks.blocked = false;
  mocks.connected = true;
  mocks.workbench = {
    snapshot: statusSnapshot(), loading: false, refreshing: false, lastFailure: undefined,
    refresh: vi.fn().mockResolvedValue(true), creatingTask: false, testingNode: undefined, sweepingCards: false,
    createTestTask: vi.fn(), runNodeTest: vi.fn(), sweepCards: vi.fn(),
  };
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});
async function click(text: string, selector = "button, [role=tab], .ant-collapse-header") {
  const node = [...container.querySelectorAll<HTMLElement>(selector)].find((item) => item.textContent === text || item.getAttribute("aria-label") === text);
  expect(node, `Missing control: ${text}`).toBeTruthy();
  await act(async () => node!.click());
}

it("marks missing background data and preserves worker evidence and pool counts in collapsed details", async () => {
  const snapshot = statusSnapshot({
    workers: { "outbox-verifier": workerObservation({ phase: "reading_source", pending: 3 }), "im-source-check": null },
    queues: { translation: { initialized: false, alive: false, pending: 0, current_started: null, current_age_s: null, last_completed: null, observed_at: 180 } },
    pools: { product_cards: 2, inquiry_cards: 4 },
  });
  await act(async () => root.render(<WorkerStatus snapshot={snapshot} />));
  expect(container.textContent).toContain("当前快照未提供统一后台数据");
  expect(container.textContent).not.toContain("待核对数量：3");
  await click("工作循环与队列观察");
  expect(container.textContent).toContain("线程运行中");
  expect(container.textContent).toContain("持续 60 秒");
  expect(container.textContent).toContain("已完成循环 2");
  expect(container.textContent).toContain("待核对数量：3");
  expect(container.textContent).toContain("尚无观察记录");
  expect(container.textContent).toContain("未启动");
  await click("缓存池计数");
  expect(container.textContent).toContain("产品卡池：2");
  expect(container.textContent).toContain("询盘卡池：4");
  await act(async () => root.render(<WorkerStatus snapshot={{ ...snapshot, workers: { "outbox-verifier": workerObservation({ alive: false, stopping: true, phase: "stopped", last_error: "OSError" }) } }} />));
  expect(container.textContent).toContain("线程已停止");
  expect(container.textContent).toContain("上次循环异常：OSError");
});

it("shows admission blocking and stopping without reporting unknown sweep counters as zero", async () => {
  const snapshot = statusSnapshot({
    workers: { "card-sweep": workerObservation({ targets: null, visited: null, enriched: null, failed: null }) },
    background: backgroundStatus({ runtime: { closing: true, observed_at: 180, resources: [
      runtimeResource({ name: "card-sweep-old", started_at: 1, state: "failed", last_error: "previous sweep worker" }),
      runtimeResource({ name: "card-sweep-1", state: "blocked", pending: 1 }),
      runtimeResource({ name: "crm-sync", started_at: 10, state: "stopped" }),
      runtimeResource({ name: "crm-sync", started_at: 20, state: "stopping", active: 1 }),
    ] } }),
  });
  await act(async () => root.render(<WorkerStatus snapshot={snapshot} />));
  expect(container.textContent).toContain("后台正在停止");
  expect(container.textContent).toContain("执行器准入阻塞");
  expect(container.textContent).toContain("执行器停止中");
  expect(container.textContent).toContain("已富化 未提供");
  expect(container.textContent).not.toContain("已富化 0");
  expect(container.textContent).not.toContain("previous sweep worker");
  await click("基础执行器详情与已结束历史");
  expect(container.textContent).toContain("previous sweep worker");
  const rows = [...container.querySelectorAll("tbody tr")];
  expect(rows.filter((row) => row.textContent?.includes("crm-sync"))).toHaveLength(2);
  expect(rows.some((row) => row.textContent?.includes("历史实例") && row.textContent.includes("已停止"))).toBe(true);
  expect(rows.some((row) => row.textContent?.includes("活动实例") && row.textContent.includes("停止中"))).toBe(true);
});

it("uses full queue counts even when the 100-row sample contains only queued tasks", async () => {
  const background = backgroundStatus({
    queueCounts: { maafw: { running: 1, failed: 5, pending: 150, succeeded: 9 }, translation: {} },
    queueTasks: Array.from({ length: 100 }, (_, index) => ({ queue: "maafw", id: `queued-${index}`, type: "GUI task", status: "queued", createdAt: "now", duration: "0s", message: "waiting" })),
  });
  const snapshot = statusSnapshot({ background });
  await act(async () => root.render(<WorkerStatus snapshot={snapshot} />));
  const guiCard = () => [...container.querySelectorAll(".ant-card")].find((card) => card.querySelector(".ant-card-head-title")?.textContent === "GUI 队列")!;
  expect(guiCard().querySelector(".ant-card-extra")?.textContent).toBe("执行中");
  expect(guiCard().textContent).toContain("保留期内全量计数");
  expect(guiCard().textContent).toContain("排队 150 · 执行 1 · 完成 9 · 失败 5");
  await act(async () => root.render(<WorkerStatus snapshot={{ ...snapshot, background: { ...background, queueCounts: undefined } }} />));
  expect(guiCard().querySelector(".ant-card-extra")?.textContent).toBe("排队中");
  expect(guiCard().textContent).toContain("仅列表样本 100 项，全量计数未提供");
  expect(guiCard().textContent).toContain("排队 100 · 执行 0 · 完成 0 · 失败 0");
});

it("keeps queue completion, cooperative cancellation and local Outbox evidence separate", async () => {
  const snapshot = statusSnapshot({ background: backgroundStatus({
    queueCounts: { maafw: { succeeded: 1 }, translation: { succeeded: 1 } },
    queueTasks: ["maafw", "translation"].map((queue) => ({ queue: queue as "maafw" | "translation", id: "shared-id", type: `${queue} task`, status: "succeeded", createdAt: "2026-11-08", duration: "2s", message: "done", result: "operation complete" })),
    experiments: [{ runId: "run-1", experimentId: "exp-1", stepIndex: 0, status: "running", cancelRequested: true, startedAt: 100, finishedAt: null, variants: [] }],
    outbox: { counts: { observed: 12, unknown: 2 }, recent: [
      { id: "observed-send", status: "observed", action: "send", conversationId: 1, createdAt: 100, updatedAt: 150, mayHaveSent: true },
      { id: "unknown-send", status: "unknown", action: "send", conversationId: 2, createdAt: 100, updatedAt: 150, mayHaveSent: true },
      { id: "filled-test", status: "filled", action: "test", conversationId: 3, createdAt: 100, updatedAt: 150, mayHaveSent: false },
    ] },
  }) });
  await act(async () => root.render(<BackgroundTasks snapshot={snapshot} />));
  const taskRows = [...container.querySelectorAll("tbody tr")].filter((row) => row.textContent?.includes("shared-id"));
  expect(taskRows).toHaveLength(2);
  expect(new Set(taskRows.map((row) => row.getAttribute("data-row-key"))).size).toBe(2);
  expect(container.textContent).toContain("GUI 发送与核查结果请查看 Outbox");
  expect(container.textContent).toContain("每类队列最多 100 条");
  expect(container.textContent).toContain("活动任务优先");
  expect(container.textContent).toContain("卡片计数覆盖保留期内全部任务，不受列表上限影响");
  await click("Agent 实验", "[role=tab]");
  expect(container.textContent).toContain("停止中，等待当前调用结束");
  expect(container.textContent).toContain("最多显示 50 条运行记录，活动任务优先");
  await click("Outbox", "[role=tab]");
  expect(container.textContent).toContain("全部 Outbox 记录，列表仅显示最近 20 条");
  const observed = [...container.querySelectorAll("tbody tr")].find((row) => row.textContent?.includes("observed-send"))!;
  expect(observed.textContent).toContain("本地发现匹配消息");
  expect(observed.textContent).toContain("本地匹配，平台送达待确认");
  expect(observed.querySelector(".ant-tag-green")).toBeNull();
  const uncertain = [...container.querySelectorAll("tbody tr")].find((row) => row.textContent?.includes("unknown-send"))!;
  expect(uncertain.textContent).toContain("结果未知");
  expect(uncertain.textContent).toContain("可能已发送，勿直接重发");
  expect(container.textContent).toContain("仅填入，未发送");
});

it("shows partial read failures instead of empty success, while retaining available queue tasks", async () => {
  const snapshot = statusSnapshot({ background: backgroundStatus({
    errors: { outbox: "store unavailable", update: "update unavailable" },
    queueTasks: [{ id: "available-task", queue: "translation", type: "translate", status: "queued", createdAt: "now", duration: "0s", message: "waiting" }],
  }) });
  await act(async () => root.render(<><WorkerStatus snapshot={snapshot} /><BackgroundTasks snapshot={snapshot} /></>));
  expect(container.textContent).toContain("available-task");
  expect(container.textContent).toContain("部分快照读取失败");
  expect(container.textContent).not.toContain("结果未知 0");
  await click("Outbox", "[role=tab]");
  expect(container.textContent).toContain("读取失败，任务数量未知");
  expect(container.textContent).not.toContain("当前工作区暂无 Outbox 任务");
});

it("keeps the stale snapshot visible, wires manual refresh and preserves diagnostic actions", async () => {
  mocks.workbench.snapshot = statusSnapshot({ tasks: [{ id: "retained-task", type: "saved diagnostic", status: "failed", createdAt: "before failure", duration: "1s", message: "recognition failed" }] });
  mocks.workbench.lastFailure = { at: 200, message: "status request failed" };
  await act(async () => root.render(<StatusPage />));
  expect(container.textContent).toContain("当前显示上次成功快照（2026-11-08 12:00:00），可能已过期");
  expect(container.textContent).toContain("retained-task");
  await click("刷新状态");
  expect(mocks.workbench.refresh).toHaveBeenCalledOnce();
  await click("连接与诊断", "[role=tab]");
  await click("检查聊天输入框");
  expect(mocks.workbench.runNodeTest).toHaveBeenCalledExactlyOnceWith("ChatInput_GoToInput");
  await click("立即扫描");
  expect(mocks.workbench.sweepCards).toHaveBeenCalledOnce();
  mocks.connected = false;
  await act(async () => root.render(<StatusPage />));
  const diagnostic = [...container.querySelectorAll("button")].find((button) => button.textContent === "检查联系人搜索框")!;
  expect(diagnostic.disabled).toBe(true);
  await click("创建测试任务");
  expect(mocks.workbench.createTestTask).toHaveBeenCalledOnce();
  mocks.blocked = true;
  await act(async () => root.render(<StatusPage />));
  const createTask = [...container.querySelectorAll("button")].find((button) => button.textContent === "创建测试任务")!;
  expect(createTask.disabled).toBe(true);
  await click("创建测试任务");
  expect(mocks.workbench.createTestTask).toHaveBeenCalledOnce();
  mocks.workbench.snapshot = undefined;
  await act(async () => root.render(<StatusPage />));
  expect(container.textContent).toContain("尚无可用快照");
  await click("刷新状态");
  expect(mocks.workbench.refresh).toHaveBeenCalledTimes(2);
});
