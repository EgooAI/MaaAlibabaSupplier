# Outbox sending contract

The chat workbench submits once and automatically navigates, fills and sends. JSON responses use the `{ code, msg, data }` envelope.

| Request | Body | Result |
| --- | --- | --- |
| `POST /api/conversations/{sid}/messages` | `{ content, content_format?: 'text', action: 'send' | 'test', idempotency_key, intent_id? }` | `{ outbox: OutboxTask }` |
| `GET /api/conversations/{sid}/outbox` | none | `OutboxTask[]` |
| `GET /api/outbox/{id}` | none | `OutboxTask` |
| `POST /api/outbox/{id}/cancel` | `{ version }` | `OutboxTask` |
| `POST /api/outbox/{id}/retry` | `{ version }` | `OutboxTask` |

`OutboxTask` is defined in `types/chatOperations.ts`. Timestamps are Unix seconds. Versions increase on state and field updates. The normal send path is `queued → navigating → running → verifying → observed`; `test` ends as `filled` after input. `unknown` can later become `observed` without replaying GUI actions.

## Execution

The send button submits; its adjacent menu offers fill-only. If an inert parse of a new draft finds HTML elements, sending first shows “检测到 HTML 元素 / HTML 文本可能不支持展示，是否仍要发送？” with “取消” and “仍要发送”. Fill-only uses “是否仍要填入？” and “仍要填入”. Confirmation submits literal text, including tags and entities; it does not select a rich-text format. Closing or cancelling preserves the draft and submits nothing. Confirmation binds the captured draft and account; later edits cannot replace its content or be cleared by its receipt. Recovery of a confirmed text submission reuses its identity without another dialog. One queue callback holds the original account/window guard across navigation, input and send. Contact search still uses login ID and Enter, including the optional dialog-dismissal node.

Chat filling uses the `PasteChatText` custom action: native `SendInput` selects all, clears with Backspace and pastes literal Unicode text. Only `CF_UNICODETEXT` is published; HTML clipboard encoding and readback have been removed. It then selects/copies the result and requires a new clipboard version owned by the bound client, comparing the full Unicode text with only CRLF/LF/CR normalization. Focus changes, partial key injection and failed readback stop the operation before sending. Successful readback clears the editor selection. The system clipboard retains the copied content. This input check is separate from post-send local-source verification below.

`shared/clipboard_payload.py` encodes formats and their ordered readback contracts without Windows access. `shared/backend/clipboard_transport.py` publishes named/numeric format byte buffers, selects the first advertised readback format under one clipboard lock, and manages native memory, ownership, focus and input. `clipboard_input.py` orchestrates text-editor selection/paste/copy. Binary image or file flavors can use the transport with their own codecs and editor interaction instead of changing text codecs.

For `send`, a valid source-message baseline is required before GUI operations. `test` does not require a baseline. Before posting the native send task, the service durably records `may_have_sent=true`. A failed or interrupted possible send is never automatically replayed. The verifier checks local-source evidence approximately every two seconds with the existing 120-second occurrence window. A local match is not platform delivery or read evidence.

Local comparison uses immutable `content_format`, defaulting old requests/rows to `text`. All new requests must use text; HTML format requests return 422. Text matches the client's raw or exact escaped literal-text representation (`&lt;`, `&gt;`, `&amp;`, `&quot;`, spaces as `&nbsp;`, newlines as `<br>`). Text containing markup-sensitive characters requires the escaped representation, so literal `<b>…</b>` cannot claim a rendered HTML send. Historical HTML tasks retain original-markup comparison, without tag aliases, stripping or plain-text fallback. Both normalize line endings and boundary whitespace only. The same rules apply to candidates and competing sends, including historical formats. Evidence retains original source text and message ID. Existing uncertain sends remain eligible for reconciliation without another send.

Only `queued` is cancellable. Retry requires `failed && !may_have_sent` and captures a new account/window token. Restart invalidates unfinished work without replay. Old `awaiting_confirmation` and `queued_send` tasks become failed on recovery, or unknown if marked possibly sent. Legacy screenshot files, nullable database columns and audit events are retained; no new confirmation images are captured. Confirmation and screenshot API routes are retired.

## Drafts and idempotency

Submission freezes text and format. The browser persists a draft revision and an intent before any network lookup or mutation. One draft revision/action owns one idempotency key. Double clicks, timeouts, busy conflicts and reload recovery reuse the key and text format. Editing the draft creates a new revision, so another message may have identical text. Explicit recovery always binds the selected saved intent. Legacy HTML intents can only recover an existing receipt: if no task is found, they are retained and rejected without a POST or format conversion. Historical HTML tasks cannot be retried. Users can edit the draft to make a new, explicitly confirmed text submission.

Local intents live under `maa:outbox-intents:[data_dir,self_ali_id,sid]`, retaining `{ key, content, contentFormat?, action, taskId?, draftId?, intentId? }`. Missing `contentFormat` means text. Existing records remain recoverable. Draft text retains its existing storage key; `:revision` stores its identity. Storage failures block submission, while draft text retains its memory fallback.

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
