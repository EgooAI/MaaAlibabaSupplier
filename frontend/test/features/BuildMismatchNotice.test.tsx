// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BuildMismatchNotice } from "@/features/settings/BuildMismatchNotice";

vi.mock("antd", () => {
  const Group = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  return {
    Space: Group,
    Typography: { Text: Group },
    Card: ({ title, children }: { title: string; children: ReactNode }) => <section>{title}{children}</section>,
    Button: ({ onClick, children }: { onClick: () => void; children: ReactNode }) => <button onClick={onClick}>{children}</button>,
  };
});

const fetchMock = vi.fn();
let root: Root;
let container: HTMLDivElement;
const render = async (content: ReactNode = <BuildMismatchNotice />) => { await act(async () => root.render(content)); };
const advance = async (ms = 30_000) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("fetch", fetchMock);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("prompts to reload when the deployed build differs", async () => {
  fetchMock.mockResolvedValue(reply({ build_id: "new" }));
  const reload = vi.fn();
  await render(<BuildMismatchNotice loadedBuildId="old" reload={reload} />);

  expect(container.textContent).toContain("程序已更新");
  expect(container.textContent).toContain("当前页面仍在运行旧版本");
  expect(fetchMock).toHaveBeenCalledExactlyOnceWith("/build-id.json", { cache: "no-store" });

  await act(async () => container.querySelector("button")!.click());
  expect(reload).toHaveBeenCalledOnce();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("keeps polling while the page matches and stops after a mismatch", async () => {
  fetchMock.mockResolvedValue(reply({ build_id: "same" }));
  await render(<BuildMismatchNotice loadedBuildId="same" />);

  expect(container.textContent).toBe("");
  await advance();
  expect(fetchMock).toHaveBeenCalledTimes(2);

  fetchMock.mockResolvedValue(reply({ build_id: "new" }));
  await advance();
  expect(container.textContent).toContain("程序已更新");

  await advance(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

it("does not check builds without an inlined identity", async () => {
  await render(<BuildMismatchNotice loadedBuildId="" />);

  expect(fetchMock).not.toHaveBeenCalled();
  expect(container.textContent).toBe("");
});

it("retries after failed or malformed reads and then reports the mismatch", async () => {
  fetchMock
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(reply({ build_id: 7 }))
    .mockResolvedValueOnce(reply({ build_id: "new" }));
  await render(<BuildMismatchNotice loadedBuildId="old" />);

  expect(container.textContent).toBe("");
  await advance();
  expect(container.textContent).toBe("");
  await advance();
  expect(container.textContent).toContain("程序已更新");
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
