# Outbox sending contract

The chat workbench submits once and automatically navigates, fills and sends. JSON responses use the `{ code, msg, data }` envelope.

| Request | Body | Result |
| --- | --- | --- |
| `POST /api/conversations/{sid}/messages` | `{ content, action: 'send' | 'test', idempotency_key, intent_id? }` | `{ outbox: OutboxTask }` |
| `GET /api/conversations/{sid}/outbox` | none | `OutboxTask[]` |
| `GET /api/outbox/{id}` | none | `OutboxTask` |
| `POST /api/outbox/{id}/cancel` | `{ version }` | `OutboxTask` |
| `POST /api/outbox/{id}/retry` | `{ version }` | `OutboxTask` |

`OutboxTask` is defined in `types/chatOperations.ts`. Timestamps are Unix seconds. Versions increase on state and field updates. The normal send path is `queued → navigating → running → verifying → observed`; `test` ends as `filled` after input. `unknown` can later become `observed` without replaying GUI actions.

## Execution

The send button directly submits; its adjacent menu offers fill-only. There are no recipient, screenshot or retry confirmation dialogs. One queue callback holds the original account/window guard across navigation, input and send. Contact search still uses login ID and Enter, including the optional dialog-dismissal node.

Chat filling uses the `PasteChatText` custom action: native `SendInput` selects all, clears with Backspace and pastes Unicode clipboard text. It then selects/copies the result and requires a new clipboard version owned by the bound client, with identical text after CRLF/LF/CR normalization. Blank lines and other characters remain significant. Focus changes, partial key injection and failed readback stop the operation before the send node. The system clipboard retains the copied text; the action does not press Enter. This input check is separate from post-send local-source verification below.

For `send`, a valid source-message baseline is required before GUI operations. `test` does not require a baseline. Before posting the native send task, the service durably records `may_have_sent=true`. A failed or interrupted possible send is never automatically replayed. The verifier checks local-source evidence approximately every two seconds with the existing 120-second occurrence window. A local match is not platform delivery or read evidence.

Local text comparison strips only leading and trailing whitespace, matching the submission rule. Internal whitespace and case remain significant. The same comparison applies to candidate messages and competing sends, so whitespace variants cannot bypass ambiguity checks. Evidence retains the original source text and exact message ID. Existing unknown tasks remain eligible for later reconciliation without another send.

Only `queued` is cancellable. Retry requires `failed && !may_have_sent` and captures a new account/window token. Restart invalidates unfinished work without replay. Old `awaiting_confirmation` and `queued_send` tasks become failed on recovery, or unknown if marked possibly sent. Legacy screenshot files, nullable database columns and audit events are retained; no new confirmation images are captured. Confirmation and screenshot API routes are retired.

## Drafts and idempotency

Submission freezes the text. The browser persists a draft revision and an intent before any network lookup or mutation. One draft revision/action owns one idempotency key. Double clicks, timeouts, busy conflicts and reload recovery reuse that key. Editing the draft creates a new revision, so another message may have identical text. Explicit recovery always binds the selected saved intent.

Local intents live under `maa:outbox-intents:[data_dir,self_ali_id,sid]`, retaining `{ key, content, action, taskId?, draftId?, intentId? }`. Existing records without the newer fields remain recoverable. Draft text retains its existing storage key; `:revision` stores its identity. Storage failures block submission, while draft text retains its memory fallback.

An accepted receipt clears only the original unchanged draft, including its saved text. A newer edit, even if it changes back to the original text, survives the receipt. Timeouts and known failed/cancelled receipts do not clear drafts. Reply-intent acceptance is idempotently recoverable through the same submission key.

## Inline presentation

Send tasks appear as temporary seller bubbles with a compact status. Failure and unknown outcomes retain their full text; details and cancellation are available in a lightweight popover. Safe failures offer direct retry. Fill-only progress/results appear near the composer. Cancellation removes the temporary bubble and displays a short notice without deleting the task.

An observed bubble stays visible until the loaded conversation contains an exact match:

```text
message.externalMid === `alibaba_icbu:${seller}:${task.matched_message_id}`
```

Matching is scoped to the current account, directory and conversation. Never merge by body or timestamp. Local observation can precede CRM commit, so `observed` alone cannot remove the bubble. Temporary tasks remain separate from canonical messages and are excluded from translation, AI context, forks and exports.

Visible workspaces poll every two seconds. The recent list is capped at 100, so known unfinished and unknown tasks outside it receive direct reads; persisted task IDs restore older records after reload. Results merge monotonically by version. Failures preserve existing state. Account suspension disables actions, and obsolete account responses are discarded. Navigating away leaves server tasks running.

## Verification boundary

Regression tests use controlled responses, temporary databases and synthetic MaaFW frames. They do not send through a real client or establish browser layout, contact recognition or platform delivery.
