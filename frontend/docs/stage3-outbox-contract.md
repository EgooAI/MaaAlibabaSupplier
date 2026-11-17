# Outbox sending contract

The chat workbench submits once and automatically navigates, fills and sends. JSON responses use the `{ code, msg, data }` envelope.

| Request | Body | Result |
| --- | --- | --- |
| `POST /api/conversations/{sid}/messages` | text: `{ content, content_format?: 'text', action, idempotency_key, intent_id? }`; image: `{ message_kind: 'image', attachment_id, action, idempotency_key }` | `{ outbox: OutboxTask }` |
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

Local text comparison uses immutable `content_format`, defaulting old requests/rows to `text`. New text requests must use text; HTML format requests return 422. Text matches the client's raw or exact escaped literal-text representation (`&lt;`, `&gt;`, `&amp;`, `&quot;`, spaces as `&nbsp;`, newlines as `<br>`). Text containing markup-sensitive characters requires the escaped representation, so literal `<b>…</b>` cannot claim a rendered HTML send. Historical HTML tasks retain original-markup comparison, without tag aliases, stripping or plain-text fallback. Both normalize line endings and boundary whitespace only. The same rules apply to candidates and competing sends, including historical formats. Evidence retains original source text and message ID. Existing uncertain sends remain eligible for reconciliation without another send.

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

## 单张图片

输入区支持选择文件和粘贴单张图片，预览后独立发送，保留文字草稿。静态 PNG/JPEG/WebP 经后端应用 EXIF 方向并标准化为 RGB/RGBA PNG。原文件和发送 PNG 各不超过 10,000,000 字节，最长边 8192 像素，总像素 2400 万。超限及动画图片被拒绝，不自动缩小图片。

- `PUT /api/conversations/{sid}/image-attachments/{id}` 接收二进制图片，同 ID/原文件幂等。
- 同路径 GET 查询附件回执，后缀 `/image` 经认证返回预览 PNG。
- 图片发送使用 `message_kind=image`、`attachment_id`，正文为空，不携带文字回复意图。`action` 仍为 `send|test`。
- Outbox 返回 `message_kind`、`attachment_id` 和安全的 `image` 元数据。历史任务默认 `message_kind=text`。

图片 Blob 和草稿版本存入 IndexedDB，提交身份写入现有 localStorage。图片键固定为 `image:{attachment_id}:{action}`。上传、预览和发送的异步续接检查原账号票据与草稿版本，旧回包不能修改新草稿。超时恢复先查询原回执，不产生新发送身份。

附件通过 CRM SDK 的 `resource` / `resource_reference` 引用统一文件对象，回执增加 `resource_id=sha256:<摘要>`。默认目录为数据库旁的 `crm.sqlite.resources/`。上传保留 24 小时临时引用，Outbox 建立时在同一事务中转为长期引用。后台每小时分批清理无有效引用对象，保留身份和摘要。迁移旧 BLOB 时保留原附件 ID 与发送摘要。

`GET /api/images/{resource_id}` 通过认证和当前账号作用域返回图片，网页使用相同账号票据获取 Blob。知道资源哈希不能代替消息或附件访问权限。现有附件和消息图片 URL 继续提供业务归属校验。

客户端新消息在 CRM 提交后自动归档图片。每个工作区首次成功同步建立历史基线，历史图片首次查看时补存。图片归档进度独立于聊天同步版本，在状态页“图片归档”中显示。平台下载保留原始编码，已归档内容本地优先；临时网络故障退避重试。发送核验复用归档资源与像素摘要，仍保留原有证据边界。

`PasteChatImage` 发布 PNG/DIBV5，最多等待 10 秒取得新版本的精确像素读回，同时要求同版本 Unicode 恰好为一个 `U+FFFC` 图片对象。缺少结构证据、残留文字或多个对象都会中止发送。文字/图片使用独立流水线节点，每次重设输入分支，避免覆写残留。

发送后保留基线、来源、联系人、方向、时间和竞争任务约束。图片宽高一致且满足“MD5 + 字节数一致”或“精确 RGBA 像素 SHA-256 一致”才可匹配。取图在账号锁和 Outbox 锁外执行，返回后重新检查账号和竞争任务。取图失败不代表未发送，不触发重发。签名 URL 不进入公开证据。

## 验证范围

Regression tests use controlled responses, temporary databases and synthetic MaaFW frames. They do not send through a real client or establish browser layout, contact recognition or platform delivery.
