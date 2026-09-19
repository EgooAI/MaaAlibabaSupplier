import { describe, expect, it } from "vitest";
import { parseInstallAcceptance, parseUpdateState } from "@/services/updateProtocol";
import type { UpdateState } from "@/types/update";

const snapshot: UpdateState = {
  supported: true, reason: null, phase: "available",
  current: { version: "v1", sha: null, run_id: 7 },
  source: { repository: "example/app", branch: "main", workflow: "build.yml", artifact: "app" },
  candidate: { id: "42:2", version: "v2", sha: "abc123", run_id: 42, run_attempt: 2, created_at: "2026-09-18T10:00:00Z", url: "https://github.com/example/app/actions/runs/42" },
  downloaded_bytes: 0, total_bytes: null, error: null,
  last_result: { status: "failed", message: "previous failure", version: null },
};

describe("update protocol", () => {
  it("accepts supported and unsupported snapshots, nullable fields, and safe count boundaries", () => {
    expect(parseUpdateState(snapshot)).toEqual(snapshot);
    const unsupported = { ...snapshot, supported: false, reason: "source build", candidate: null, last_result: null, downloaded_bytes: Number.MAX_SAFE_INTEGER, total_bytes: Number.MAX_SAFE_INTEGER };
    expect(parseUpdateState(unsupported)).toEqual(unsupported);
    expect(parseInstallAcceptance({ accepted: true })).toEqual({ accepted: true });
  });

  it.each([
    "supported", "reason", "phase", "current", "current.version", "current.sha",
    "source", "source.repository", "source.branch", "source.workflow", "source.artifact",
    "candidate", "candidate.id", "candidate.version", "candidate.sha", "candidate.run_id", "candidate.run_attempt", "candidate.created_at", "candidate.url",
    "downloaded_bytes", "total_bytes", "error", "last_result", "last_result.status", "last_result.message", "last_result.version",
  ])("rejects a missing required field: %s", (path) => {
    const value = structuredClone(snapshot) as unknown as Record<string, unknown>;
    const [parent, child] = path.split(".");
    if (child) delete (value[parent] as Record<string, unknown>)[child];
    else delete value[parent];
    expect(() => parseUpdateState(value)).toThrow("更新状态响应无效");
  });

  it.each([
    { supported: "true" }, { reason: false }, { phase: "complete" },
    { current: null }, { current: [] }, { current: { version: 1, sha: null } }, { current: { version: "v1", sha: 123 } },
    { source: null }, { source: { ...snapshot.source, branch: false } },
    { candidate: [] }, { candidate: { ...snapshot.candidate, url: {} } },
    { error: [] }, { last_result: { status: false, message: "failure", version: null } },
    { last_result: { status: "failed", message: {}, version: 1 } },
  ])("rejects incorrectly typed fields: %j", (fields) => {
    expect(() => parseUpdateState({ ...snapshot, ...fields })).toThrow("更新状态响应无效");
  });

  it.each(["1024", NaN, Infinity, -Infinity, -1, 0.5, Number.MAX_SAFE_INTEGER + 1])("rejects invalid byte and run counts: %s", (count) => {
    for (const field of ["downloaded_bytes", "total_bytes"]) {
      expect(() => parseUpdateState({ ...snapshot, [field]: count })).toThrow("更新状态响应无效");
    }
    for (const field of ["run_id", "run_attempt"]) {
      expect(() => parseUpdateState({ ...snapshot, candidate: { ...snapshot.candidate, [field]: count } })).toThrow("更新状态响应无效");
    }
    expect(() => parseUpdateState({ ...snapshot, current: { ...snapshot.current, run_id: count } })).toThrow("更新状态响应无效");
  });
});
