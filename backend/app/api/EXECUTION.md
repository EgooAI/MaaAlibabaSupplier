# 远程执行

系统状态页的“远程执行”以认证 API 提交 Python 脚本，使用软件自带的解释器运行。它属于遥测与诊断能力，用于在后端、聊天同步或 GUI 操作之外读取本机状态。该功能不依赖 PowerShell 7；需要查询 Windows 事件时，脚本可以自行调用系统自带的 `powershell.exe`。

## 安全边界

- 脚本以运行后端的同一 Windows 用户权限执行，可读写文件、访问网络并启动其他程序。这是完整的远程代码执行能力；独立子进程只提供故障隔离和生命周期管理，不是权限沙箱。
- 每次后端启动都恢复为关闭状态，不写入常驻配置。必须由网页或 API 显式开启。
- 关闭开关、停止任务、执行超时或程序退出都会终止整个 Python 进程树（Windows Job Object，`KILL_ON_JOB_CLOSE`）。非 Windows 环境回退为终止直接子进程。
- 鉴权沿用现有 `Authorization: Bearer` 会话。这些接口不绑定卖家、数据目录或账号代次，因为后端不可用或未接入账号时仍需要本机诊断。
- 执行任务占用后台运行时准入，因此更新安装不会在脚本运行期间替换程序文件。

## 接口

| 请求 | 说明 |
| --- | --- |
| `GET /api/app/diagnostics/execution` | 开关、代次、启动标识、解释器、工作目录、限制与最近任务 |
| `PUT /api/app/diagnostics/execution` | `{ enabled, generation }`；代次不符返回 409 |
| `GET /api/app/diagnostics/execution/runs?limit=` | 最近任务摘要 |
| `POST /api/app/diagnostics/execution/runs` | `{ id, start_token, generation, script, timeout_s }` |
| `GET /api/app/diagnostics/execution/runs/{id}?stdout_offset=&stderr_offset=` | 任务详情与增量输出 |
| `POST /api/app/diagnostics/execution/runs/{id}/cancel` | 停止未结束的任务；已结束任务幂等返回 |

`state.start_token` 在每次后端启动时随机生成。提交必须同时匹配当前 `start_token` 与开关 `generation`，因此重启、关闭再开启之后的迟到请求不会执行。`id` 为客户端生成的 32 位十六进制标识：相同标识与相同脚本、超时返回原任务，不重复执行；相同标识配不同负载返回 409。

## 限制与状态

| 项目 | 值 |
| --- | --- |
| 脚本 | 最多 64 KiB UTF-8 |
| 超时 | 1–300 秒，默认 60 |
| 输出 | stdout、stderr 各最多 1 MiB 字符，超出继续排空并标记截断 |
| 并发 | 同时 1 个任务 |
| 历史 | 最近 20 条、24 小时 |

状态：`queued`、`running`、`succeeded`、`failed`、`cancelled`、`timeout`、`interrupted`。`succeeded` 只表示进程以 0 退出，不代表脚本完成任何业务目标。停止或超时后若进程树未在 5 秒内退出，状态记为 `failed / termination_unconfirmed`，并继续保持占用，避免在旧脚本仍运行时启动第二个脚本。

## 存储与日志

运行记录写入 `backend/data/diagnostics/execution/`：`runs.json` 保存元数据，`<id>.py` 保存脚本，`<id>.stdout.txt` / `<id>.stderr.txt` 保存完整输出。它们不进入普通日志，也不受日志轮转影响，但仍受本功能的 20 条 / 24 小时清理约束。重启时未结束的任务标记为 `interrupted`，不自动重放。脚本输出可能包含敏感信息，仅保存在本机，经认证接口读取。

普通日志只记录 `execution.switch`、`execution.submitted`、`execution.finished` 等结构化生命周期事件和固定原因码，不记录脚本正文或输出。诊断 ZIP 只包含开关与计数摘要，不打包脚本和原始输出。

## 输出读取

输出以 UTF-8 文本写入文件并按字符计数。客户端用 `stdout_offset` / `stderr_offset` 读取增量；响应返回新的偏移和总长度。文件在读取期间可能继续增长，未完成的任务每轮读到的内容都是当前前缀。

## 验证范围

`backend/tests/test_execution_service.py` 用替换的进程边界覆盖开关代次、启动标识、幂等、单并发、取消、超时、输出截断、非零退出、重启中断、历史清理与认证接口。它不启动真实脚本，也不验证真实 Windows Job Object 对孙进程的清理。真实解释器、权限与桌面环境需要在目标机器上以“仅运行 `print`”的最小脚本单独确认。
