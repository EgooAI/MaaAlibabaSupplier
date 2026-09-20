# Stage 3 Outbox

The chat workbench uses the outbox contract exclusively. JSON responses use the existing `{ code, msg, data }` envelope; the shapes below describe `data`.

| Request | Body | Result |
| --- | --- | --- |
| `POST /api/conversations/{sid}/messages` | `{ content, action: 'send' | 'test', idempotency_key, draft_version? }` | `{ outbox: OutboxTask }` |
| `GET /api/conversations/{sid}/outbox` | none | `OutboxTask[]` |
| `GET /api/outbox/{id}` | none | `OutboxTask` |
| `POST /api/outbox/{id}/confirm` | `{ version, screenshot_id }` | `OutboxTask` |
| `POST /api/outbox/{id}/cancel` | `{ version }` | `OutboxTask` |
| `POST /api/outbox/{id}/retry` | `{ version }` | `OutboxTask` |
| `GET /api/outbox/{id}/screenshot/{screenshot_id}?version={version}` | none | PNG bytes, never a JSON envelope |

`OutboxTask` is defined in `types/chatOperations.ts`. Timestamps are Unix seconds. Task versions must increase on state or screenshot changes. Only `observed`, `filled`, `failed`, `unknown`, and `cancelled` are terminal. There is no manual `confirmed` terminal state. `observed` is displayed as local matching-message evidence, never proof of delivery.

The first dialog freezes the draft and asks the user to check seller, recipient name and login ID. Submission starts blind contact search. The backend must stop at `awaiting_confirmation` before input or send. Opening the second dialog reads the current task, then fetches its actual PNG with `X-Account-Epoch` and `cache: no-store`. The response must have an explicit matching epoch and PNG content type. Confirmation stays disabled until the image loads, and binds the exact displayed task version and screenshot ID. A final task GET checks for changes before the confirm POST; the backend remains responsible for atomic version checks and rechecking the actual GUI.

Screenshots expire after 120 seconds. Changed or expired frames require another user review; reads never capture a new frame or confirm automatically. Refresh is read-only. If an expired task remains awaiting confirmation, it can be cancelled and deliberately recreated; only `failed && !may_have_sent` allows retry and fresh capture. Cancel is offered only for `queued`, `awaiting_confirmation`, and `queued_send`. Retry always has its own confirmation dialog. Blob URLs are revoked on close, expiry, replacement, workspace unmount and account suspension/switch.

The account epoch applies to all outbox endpoints. Send, confirm and retry require `operate_client`: a selected seller and a connected client. There is no separate seller-confirmation step. The backend captures seller, directory, epoch and window generation and rechecks them during execution; the outbox screenshot and content confirmation above remains required. Cancel and reads do not require a connected client. A blocked account preserves visible records and suspends requests/actions until account observation recovers. Obsolete responses are discarded by the existing account-scoped backend.

Visible workbenches poll the conversation outbox every two seconds, without GUI side effects. The recent list is capped at 100 records ordered by creation time, so known unfinished tasks outside that list also receive `GET /api/outbox/{id}` reads. Partial read failures preserve existing records, and all results merge monotonically by version under the original account scope. Navigating away leaves server tasks running.

Local intent records are stored under `maa:outbox-intents:[data_dir,self_ali_id,sid]`. They retain `{ key, content, action, taskId? }`, including after timeouts and terminal results. Task IDs are added when receipts or matching GET results arrive; retry/cancel also remember the selected task ID before the mutation. Existing records without IDs remain readable. On reload, missing recent-list entries are recovered by saved task ID, including older retried tasks. Unknown IDs are never guessed: the user can explicitly recover an unresolved intent by POSTing its original key, which also handles a lost response outside the recent list. No by-key endpoint is required.

Repeated identical intents reuse the saved key and first reconcile through GET. Only the explicit **New Send** action allocates a new key for identical terminal content. That key is created once when the user opens its confirmation dialog and persisted before the first API call; rerenders, failed lookups, lost POST responses, busy 409 responses, and repeated confirmations all reuse it, even if the corresponding task is already terminal. Recovery buttons bind the specific saved intent rather than selecting another intent with the same text. A busy 409 is shown as a submission conflict and never fabricates a failed task. Storage failures block submission; drafts retain their existing memory fallback. Drafts are never automatically cleared.

Tests use the existing Vitest/happy-dom setup. A synthetic one-pixel PNG validates binary fetching and image lifecycle, not recognition of a real client contact. Live integration still requires a connected client and backend routes implementing this contract; no automatic test confirms or sends through the real client.
