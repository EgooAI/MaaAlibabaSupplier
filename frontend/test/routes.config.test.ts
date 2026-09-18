import { describe, expect, it } from "vitest";
import { resolveOpenKeys, resolveSelectedKey } from "@/components/routes.config";

describe("routes single source", () => {
  it("resolves selected keys without falling back to raw top segment", () => {
    expect(resolveSelectedKey("/")).toBe("/");
    expect(resolveSelectedKey("/chat")).toBe("/chat/customer-sessions");
    expect(resolveSelectedKey("/chat/customer-sessions")).toBe("/chat/customer-sessions");
    expect(resolveSelectedKey("/chat/agent-sessions")).toBe("/chat/agent-sessions");
    expect(resolveSelectedKey("/agent")).toBe("/agent/llm");
    expect(resolveSelectedKey("/agent/llm")).toBe("/agent/llm");
    expect(resolveSelectedKey("/status")).toBe("/status");
    expect(resolveSelectedKey("/settings")).toBe("/settings");
  });

  it("resolves open keys to a single nav group", () => {
    expect(resolveOpenKeys("/chat/customer-sessions")).toEqual(["/chat"]);
    expect(resolveOpenKeys("/agent/llm")).toEqual(["/agent"]);
    expect(resolveOpenKeys("/status")).toEqual(["settings"]);
    expect(resolveOpenKeys("/")).toEqual([]);
  });
});
