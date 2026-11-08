# 后台任务

应用侧后台执行统一由 `background.py` 的 `BackgroundRuntime` 管理。运行时处理准入、调度、停止和等待，不依赖账号、GUI、数据库或 FastAPI。业务模块保留自己的状态机、重试策略和结果存储。

## 三种入口

| 入口 | 用途 | 停止方式 |
| --- | --- | --- |
| `runtime.worker(name, run, on_stop=...)` | 单次异步操作、阻塞服务 | `run(stop_event)` 协作停止，可用 `on_stop` 接入已有取消协议 |
| `runtime.schedule(name, action, interval=..., delay=...)` | 定时检查或手动触发 | 停止后丢弃未执行的触发，等待正在执行的调用 |
| `runtime.pool(name, max_workers=...)` | 返回 Future 的排队调用 | 拒绝新提交，排空已接受工作，也可显式取消未开始的 Future |

Worker 创建后调用 `start()`，线程启动时登记到运行时。Pool 在首次提交时登记，内部 worker 由 pool 单独持有。`request_stop()` 发出停止请求，`join(timeout)` 返回是否真正结束，`stop(timeout)` 在未结束时抛出 `TimeoutError`。已停止的 Worker 不可重新启动，服务需要重新创建 Worker。

Pool 的等待覆盖 Future 完成回调。Pool worker 使用非 daemon 线程，正常退出不会因为等待超时而丢弃已接受的 CRM 写入。阻塞的模型或原生调用不能被 Python 强制打断，超时仍保留运行中的 handle。

## 定时与触发

```python
runtime = get_background_runtime()

# 每次执行结束后等待 10 秒，也允许提前触发。
checker = runtime.schedule("checker", check_once, interval=10).start()
checker.trigger()

# 只由事件触发。
sweep = runtime.schedule("sweep", sweep_once, delay=None).start()
sweep.trigger()
```

`delay` 是首次执行前的等待，默认立即执行。`delay=None` 表示首次等待手动触发，`interval=None` 表示执行结束后等待下一次触发。`action()` 返回数字时，覆盖下一轮等待时间，更新器用它实现失败退避。

同一个 schedule 不会重叠执行。等待期间的多个触发合并为一次，执行期间的触发最多保留一次补跑。需要逐条排队的请求应使用 pool 或现有 `TaskQueue`，不应放入合并式 schedule。

## 业务准入

`schedule(..., ready=predicate)` 接受一个快速、无副作用的准入函数。运行时在每次执行前调用它：

```python
task = runtime.schedule(
    "maintenance", maintain_once, interval=60, ready=idle_policy.can_run,
).start()
```

返回 False 或抛出异常时，任务保持待执行状态，每秒重新检查。策略状态变化后也可以调用 `task.trigger()` 提前检查。这个接口同时适用于定时任务和手动触发任务，不会中断已经开始的操作。已接受但等待准入的任务仍阻止安装。

## 空闲与安装互斥

`activity()` 在接收任务时预留执行资格，`close()` 或退出 `with` 时释放。Worker 默认覆盖整个生命周期，pool 覆盖排队、调用和完成回调，TaskQueue 覆盖入队至任务结果记录。非驻留 schedule 的显式触发也立即预留资格，合并的补跑与 `ready` 重试保持预留。API 写请求从通过认证到响应结束占用资格，安装接口自身除外。诊断 ZIP 的 GET 单独占用资格，直到打包 worker 与响应都结束；客户端取消不能提前释放仍在打包的工作。

常驻监听、日志采集和队列消费线程使用 `worker(..., resident=True)`，不能因线程一直存活而阻止安装。IM 源检查和 Outbox 核查使用 `schedule(..., resident=True)`，每轮执行参与互斥但不重置空闲计时；它们提交的 CRM 或 GUI 工作正常计时。新增的一次性业务工作不得标记为常驻。

`begin_install(idle_for=..., ready_since=...)` 在准入锁下同时检查无预留工作、连续空闲时间及更新包就绪时间，然后关闭准入。自动安装要求 900 秒，手动安装要求 0 秒。线程轮询只能观察到结果，不能代替这次原子检查。新任务被拒绝，已存在 schedule 的定时执行暂停。`end_install()` 在失败或取消时恢复准入并重置计时。

`control=True` 仅供更新调度控制器、安装决策及握手监测使用，使它们能在安装锁内继续运行。更新检查的实际网络操作仍用 `activity()` 占用资格。已有就绪包时暂停定时远程检查，避免每 5 分钟的检查使 15 分钟空闲条件无法满足。

## 生命周期与上下文

- 主程序和独立 Maa Agent 入口最终调用 `shutdown_background()`，启动中途失败也执行清理。
- 运行时先关闭新工作准入，再执行业务清理，最后通知剩余 worker 停止并等待。一个清理失败不会跳过其余清理，错误汇总为 `ExceptionGroup`。
- `on_shutdown(name, callback, priority=...)` 注册业务清理。高优先级先执行，同优先级按注册顺序逆序执行。当前顺序为更新器 50、卡片扫描 40、Outbox 30、GUI/翻译队列 20、同步服务 10，最后释放 MaaFW、Yak 和 receiver。子进程创建后立即注册清理，日志线程等待输出结束。
- Worker 使用干净的 Context，长期共享线程不会继承创建者的账号。账号相关工作必须显式捕获并校验上下文。Pool 则为每次提交复制 Context，任务及其 worker 内完成回调使用同一份上下文。
- GUI 队列继续使用账号 epoch、窗口代次和执行锁进行实时校验。运行时的准入函数不能替代这些业务校验。

## 已接入的模块

| 模块 | 执行方式 | 业务保留职责 |
| --- | --- | --- |
| IM 同步 | schedule + 单 worker CRM pool | 源快照合并、失败退避、提交版本与账号隔离 |
| GUI / 翻译 | TaskQueue 的受管 Worker | FIFO、容量、任务快照、译文分片 |
| Outbox | GUI 队列 + verifier schedule | 持久化状态、截图确认、发送前屏障与本地证据 |
| 产品卡补全 | 仅触发的 schedule | 目标选择、GUI 守卫、会话间停止检查 |
| Agent 实验 | 每个 run 一个 Worker | SDK 内变体并行、回合取消、结果持久化 |
| 更新器 | schedule + 单次 Worker | 下载校验、安装握手、辅助进程监测 |
| receiver / 子进程日志 / Maa Agent 等待 | Worker + 资源清理回调 | 网络与原生资源自身的关闭协议 |

SDK 保持独立，其内部执行器由 SDK 调用范围管理，不反向依赖宿主运行时。前端轮询由 React 页面生命周期管理，只负责观察业务状态。任务持久化仍由各业务决定，运行时不重放重启前的 GUI 或模型任务。

## 被动观测

`runtime.observation()` 读取受管资源的执行状态和有限的结束历史，不执行任务、不调用准入函数，也不等待业务操作完成。Pool 内部 worker 由 pool 汇总，避免重复计数。运行时只保留观测数据，结束历史不持有任务闭包。

系统状态接口将运行时观察与业务结果组合：线程存活、循环结束和 Future 完成都不能替代 CRM 提交、翻译结果或 Outbox 证据。业务任务按工作区过滤，进程级执行器指标单独标明范围，详见 [系统状态契约](api/STATUS.md)。
