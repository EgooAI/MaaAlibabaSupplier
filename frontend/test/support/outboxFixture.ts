import type { OutboxTask } from "@/types/chatOperations";

export function outboxTask(overrides: Partial<OutboxTask> = {}): OutboxTask {
  return {
    id: "outbox-1", conversation_id: 42, contact_ali_id: "buyer-ali", login_id: "buyer-login", content: "submitted text", action: "send",
    status: "queued", version: 1, attempt: 1, may_have_sent: false, reason: null,
    created_at: Date.now() / 1000, updated_at: Date.now() / 1000, screenshot_id: null, screenshot_at: null, matched_message_id: null,
    idempotency_key: "intent-1", ...overrides,
  };
}

// A synthetic one-pixel PNG used only to test the authenticated blob lifecycle.
export const screenshotPng = () => new Blob([Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/RkAAAAASUVORK5CYII="), (c) => c.charCodeAt(0))], { type: "image/png" });
