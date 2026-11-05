# Stage 4 Inbox Frontend Contract

The frontend targets the paginated inbox API directly. There is no array-response compatibility path. Transport fields use snake_case; conversation view models adapt inbox state to camelCase.

## Requests

| Endpoint | Request | Response data |
| --- | --- | --- |
| `GET /api/conversations` | `q`, `search_scope=all/customer/messages`, `country`, `tag`, `reply_state`, `unread`, `offset`, `limit`, `pagination_revision` | `{items,total,offset,limit,inbox_revision,pagination_revision}` |
| `GET /api/conversations/{id}` | Existing detail request | Aggregate with flat inbox fields and opaque `read_snapshot` |
| `POST /api/conversations/{id}/read` | `{read_snapshot}` | `{state,inbox_revision}`; state includes flat inbox fields, `read_seq`, `snapshot_seq` |
| `GET /api/inbox/overview` | No query | `{counts:{total,unread,needs_reply,history_pending,waiting_customer},inbox_revision,updated_at}` |
| `GET /api/conversations/revision` | Existing revision request | Existing sync fields plus `inbox_revision` |

All endpoints retain the existing `{code,msg,data}` envelope. Conversation and inbox paths use account epoch checks, including paths containing a query string.

## State And Read Semantics

Summary/detail fields are `unread_count`, `reply_state`, `pending_since`, `history_pending`, and `uncertain`. `pending_since` and `updated_at` are Unix seconds; `pending_since` is null when there is no new unanswered buyer message. The UI derives the waiting duration ("已等待 N 分钟/小时/天") from `pending_since` and the browser clock, only for `needs_reply`. Reply states are `needs_reply`, `waiting_customer`, `history_pending`, `none`, and `unknown`.

The UI shows local unread independently from reply state. Historical baseline data may be unread zero and still `history_pending`; history has no waiting duration. Legacy status/priority values are not used for conversation business state. Profile tags are read/filter inputs only.

Only the detail button labelled `标记工作台已读` submits a read acknowledgement. Opening, selecting, focusing, translating, exporting and polling do not acknowledge reads. The button captures the displayed detail token before awaiting; it merges the returned state and triggers current list/detail reads. The backend must preserve unread messages beyond that snapshot and must not clear pending reply state.

## Pagination And Refresh

The UI requests 50 items per page. Advancing beyond offset zero sends the previous response's opaque `pagination_revision`; a 409 discards the page and reloads offset zero. Query/page changes clear selection and invalidate old list requests. Pages replace each other, never append. Rendering preserves backend item order, including numeric SID ties. Explicit page/filter changes do not replace the selected detail, draft or outbox workspace.

Visible workspaces observe revisions every 10 seconds. Changes to either message or inbox revision reload list/detail. Overview reads its own account-wide counts every 10 seconds and on visibility/recovery. The displayed waiting duration is computed at render time and therefore advances only when the view re-renders, for example after a poll that changes data.

List responses and revision polls share a monotonic inbox revision observation. A list response that observes either change schedules a separate detail revalidation, including when the active conversation is outside the new page. It does not reload that list again or reset the active conversation. Polls schedule both reads when needed. Observation is not a read acknowledgement: list/detail requests each acknowledge success, and failures remain eligible for recovery at the same revision. `read_snapshot` is refreshed only with its detail response and is submitted only by the explicit read button.

Transient failures preserve the current detail and draft. Account changes discard old responses. Background reloads preserve batch selection; explicit page/filter changes clear it. Export uses only explicitly selected IDs on the current page.

## Entry Points

Ready archives show the home dashboard and sync status; accounts without readable data retain account setup. Dashboard links use the same filter parser as the chat page:

- Pending: `/chat/customer-sessions?reply_state=needs_reply`
- Local unread: `/chat/customer-sessions?unread=true`
- Historical: `/chat/customer-sessions?reply_state=history_pending`
- Waiting: `/chat/customer-sessions?reply_state=waiting_customer`

Search is submitted explicitly. `q` is transported with `URLSearchParams`, preserving literal `%` and `_`; SQL wildcard escaping belongs to the backend. Country and existing profile tag filters are free inputs, with no claim to enumerate all available values.

P1 stage editing, custom tags and follow-ups are outside this change.

## Integration Notes

The backend implementation and `backend/app/api/INBOX.md` match the receipt above: the read route returns the store state, including `read_seq` and `snapshot_seq`, within the `{code,msg,data}` envelope. The frontend consumes the five public state fields; sequence values do not replace the signed `read_snapshot`.

Selecting a new source directory does not adopt another directory's messages or finish an empty history baseline merely by opening a page. Until the directory has an eligible archive or its first complete source sync, inbox reads return 503 with a setup/retry explanation.
