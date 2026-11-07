import type { BackgroundStatus, RuntimeResource, SystemStatusSnapshot, WorkerObservation } from "@/types/status";

export function runtimeResource(overrides: Partial<RuntimeResource> = {}): RuntimeResource {
  return {
    name: "crm-sync", kind: "pool", state: "waiting", active: 0, pending: 0, completed: 0, failed: 0,
    started_at: 100, last_started_at: null, last_completed_at: null, next_run_at: null, last_error: null,
    ...overrides,
  };
}

export function backgroundStatus(overrides: Partial<BackgroundStatus> = {}): BackgroundStatus {
  return {
    runtime: { closing: false, observed_at: 180, resources: [] }, queueTasks: [], experiments: [],
    outbox: { counts: {}, recent: [] }, update: null, errors: {}, ...overrides,
  };
}

export function statusSnapshot(overrides: Partial<SystemStatusSnapshot> = {}): SystemStatusSnapshot {
  return { updatedAt: "2026-11-08 12:00:00", modules: [], tasks: [], ...overrides };
}

export function workerObservation(overrides: Partial<WorkerObservation> = {}): WorkerObservation {
  return {
    started: true, alive: true, stopping: false, started_at: 100, heartbeat_at: 120,
    last_progress_at: 110, observed_at: 180, phase: "waiting", phase_started_at: 120,
    phase_age_s: 60, completed_iterations: 2, pending: null, context: null, last_error: null,
    progress_unit: "iterations", ...overrides,
  };
}
