# App Codebase Overview

当前文件夹 `app/`（即仓库内 `backend/app/`）中，存放着本项目的主 Python 应用。它负责拉起和调度若干独立的服务，并在不同服务间进行数据协调，以实现复杂的功能逻辑。

## 代码结构

- `shared` — 主后端，`app` 与 agent 共享。含以下子包：
  - `backend/` — 业务逻辑：邮件（`email.py`）、IM 访问（`im_chat_db.py` / `im_db_middleware.py`）、目录布局（`im_layout.py`）、系统状态（`status.py`）。
  - `agent/` — Chat/Agent 工具层：系统 Agent 身份常量（`system_agents.py`）、输入构造（`inputs.py`）、统一执行（`runner.py`，结构化 `ChatAgentTurn` + 薄封装 `run_chat_tool_agent`，支持未保存草稿预设与运行上下文）、回复建议（`suggestions.py`，由 `reply_customer` 意图驱动）、回复工具（`reply_tool.py`，效果类工具与参数校验）、效果分发（`effect_dispatch.py`，live 落库 / simulated 只记录 / 无作用域拒绝）、翻译写入 CRM（`translation.py`，短哈希标记与三值协议）、系统预设补种（`system_presets.py`，指纹匹配、不覆盖用户编辑、写入 `requires_tool`）、预设版本历史（`preset_history.py`）、工具清单与未注册工具判定（`tools.py`）、输出归一化（`output_normalizers.py`，仅剩翻译与分析类，回复建议已不再使用）。
  - `crm/` — 应用侧 CRM 适配层。`__init__.py` 是 API/业务稳定入口；`sync.py` 的 `CRMAdapter` 直接承担 SDK 读写（路由不再经过转发层）；`paths.py` 解析 CRM 库路径；`outbox_state.py` 是出站状态、转换表、可编辑字段与风险判定的单一来源；`translation_cache.py` 译文表读写（32 位 md5 主键，`get_translation()` 的空串为 NO_NEED 哨兵，`get_translations()` 批量复用同一 Manager）；`message_markdown.py` 是消息 → Markdown 的唯一投影（LLM 输入与聊天导出共用，卡片结构化取 `shared/cards.py`，正文不转义以保翻译保真）。
  - `utils/` — 环境变量、IM 解密、WAL 处理、日志等。
  - `mitm/` — MITM 解析器与数据池。
- `crm_sdk` — 通用 CRM SDK（仓库内直接引用，包化改造待做）；应用专属逻辑放 `shared/crm/` 与 `shared/agent/`，不要写入 SDK。
- `agent` — Maa Custom Recognition/Action 入口。
- `api` — FastAPI：`main.py:create_app()` + `server.py:run()`；`routers/` 按功能分模块（conversations / messages / settings / status / outbox / agent / self / auth / app）。Web UI 见仓库根 `frontend/`（Next.js，`pnpm dev`，将 `/api/*` 反代到本服务）。
- `mitm` — Yak MITM receiver（`proxy.py`）。
- `background.py` — 后台运行时：统一 Worker、定时/触发调度、Future 线程池与退出清理。任务准入与安装互斥共用锁，记录非常驻工作的连续空闲时间。接入方式与生命周期约定见 [后台任务](BACKGROUND.md)。

## 启动流程

后端唯一启动方式：仓库根运行 `python -m backend.app.main`。启动时依次：

1. 加载 `.env`（`load_workdir_env()`，从 cwd 向上查找，兜底仓库根）；
2. 验证必填的固定密钥摘要 `MAA_AUTH_SECRET_SHA256` 和 MITM 回环地址，生成本次启动的内部凭据，再配置日志（`configure_logging()`）并注册更新器运行环境（`register_update_runtime()`）。受支持的安装版在启动业务子进程前建立 Windows Job Object，并按配置启用自动更新检查；
3. 播种系统 Agent（`ensure_system_agents_seeded()`：指纹匹配，只补缺失行或仍等于内置默认的记录，用户编辑不被启动覆盖）与默认 LLM 层级（`ensure_default_llm_levels_seeded()`，仅补缺失的 Level 0 空行）并注册 LLM/工具/输出归一化（幂等，仅启动时一次）；
4. 绑定并启动 MITM Python receiver 线程（`MITM_RECEIVER_HOST/PORT`，默认 `127.0.0.1:8085`，仅允许回环地址），绑定失败时不启动 GUI；
5. 启动 MaaFW 子进程：可执行文件固定为 `backend/assets/MaaPiCli.exe`（由 `tools/install_3_maafw.py` 安装），同目录的 `MaaUtils.dll` 用于定位项目，其他运行库通过子进程 PATH 从 `backend/deps/bin` 加载。框架和 Python 绑定固定为 5.14.3，MaaPiCli 使用该版本主包自带的程序；
6. 启动 Yak MITM 代理（脚本 `backend/yak_mitm.yak`，默认 `127.0.0.1:8084`）。Yak 发现顺序：`YAK_EXECUTABLE` → PATH 上的 `yak` → `backend/.portable/yak/yak.exe`（`tools/install_4_yak.py` 的产物）；
7. 启动 IM 同步、Outbox 核查和手动卡片扫描服务，再启动 HTTP API（主线程阻塞，uvicorn，`MAA_API_HOST/PORT` 默认 `127.0.0.1:8000`，供 `frontend/` Next.js 反代 `/api/*`）。后台工作统一登记到运行时，退出或启动失败时按依赖顺序清理，等待已提交的 CRM 工作完成。

## 后台任务设计原则

应用侧后台执行统一使用 `background.py` 的 `BackgroundRuntime`。新增后台工作应复用下列入口，业务模块不自行创建未受管的线程、线程池或定时循环。

| 入口 | 适用场景 |
| --- | --- |
| `runtime.worker(name, run, on_stop=...)` | 单次异步操作、阻塞服务，通过 `run(stop_event)` 或已有取消协议协作停止 |
| `runtime.schedule(name, action, interval=..., delay=..., ready=...)` | 定时检查与事件触发，返回的 Worker 通过 `trigger()` 接收触发 |
| `runtime.pool(name, max_workers=...)` | 逐条排队并返回 Future 的工作，默认单 worker，按业务需要设置并发度 |

1. **统一生命周期。** Worker 启动、pool 首次提交时登记，业务清理通过 `on_shutdown()` 注册。主程序和独立 Maa Agent 入口在正常退出或启动失败时调用 `shutdown_background()`，关闭新工作准入，按依赖顺序清理，再停止并等待剩余工作。单个清理失败不跳过其他清理。停止超时保留运行中的 handle，不能把停止请求当作已经结束。
2. **保持机制精简。** 复用 Worker、schedule 和 pool，服务只实现一次业务操作及必要的状态管理。已有 `TaskQueue` 在受管 Worker 上提供 FIFO、容量限制和任务快照，GUI 与翻译继续使用各自的队列。
3. **保持业务低耦合。** 运行时不依赖账号、GUI、数据库或 FastAPI。业务模块负责状态机、重试策略、幂等和持久化。SDK 保持独立，其内部执行器不反向依赖宿主运行时。前端轮询由页面生命周期管理，只负责观察状态。
4. **同时支持定时与触发。** `interval` 表示每次执行结束后的等待时间，`delay` 控制首次等待。仅触发任务使用 `delay=None` 并省略 `interval`。同一 schedule 不重叠执行，重复触发合并，执行期间最多保留一次补跑。需要逐条执行的请求使用 pool 或 `TaskQueue`。业务可以返回下一轮等待时间，实现退避。
5. **统一空闲计时与安装互斥。** 一次性 Worker、队列、pool 和非驻留 schedule 在接收工作时占用准入，直到完成及回调结束。常驻线程显式标记 `resident=True`，常驻轮询的执行仍参与互斥，但不重置空闲时间。安装以同一把锁检查空闲并关闭准入，避免检查后又启动任务。`ready` 仍用于业务准入，返回 False 或抛出异常时保留待办并重试，不能代替安装互斥或业务校验。
6. **明确上下文与完成边界。** 长期 Worker 使用干净的 Context，账号相关任务须显式捕获并校验原账号，避免继承创建者的身份。Pool 为每次提交复制 Context，任务及 worker 内的完成回调使用该上下文，等待覆盖完成回调。数据提交、GUI 操作完成与平台送达仍由业务分别确认，重启不自动重放 GUI 或模型任务。

当前同步检查、Outbox 核查和自动更新使用定时 schedule，产品卡补全使用仅触发的 schedule。CRM 写入使用单 worker pool，实验运行、更新握手、receiver、子进程日志采集和 Maa Agent 等待使用受管 Worker。接口示例、关闭顺序与模块职责见 [后台任务接入说明](BACKGROUND.md)。

系统状态页通过 `GET /api/status` 的 `background` 观察运行时、GUI/翻译任务、当前工作区的实验与 Outbox，以及更新进度。进程执行器计数与账号业务结果分别展示，准入阻塞、停止中、正常停止和失败使用不同状态。观察不启动工作，局部读取失败显示未知，网络失败保留带过期提示的最后快照。字段与页面生命周期见 [系统状态契约](api/STATUS.md)。

## MITM 模块设计

### 架构

```text
APP -> 127.0.0.1:8084 Yak/Yakit MITM -> 127.0.0.1:8085 Python receiver -> parsers/pool
```

- `proxy.py` — receiver 生命周期；
- `parsers.py` — API 响应解析；
- `pool.py` — 线程安全池（部分 SQLite 持久化）。

### 池子

部分池持久化到 `data/pools.db`。客户/会话/消息主数据经 `app.shared.crm` 读取，不直接依赖 pool。

- **UserInfoPool** — 联系人合并缓存；
- **ProductCardPool** — `fetchcard` 产品卡；卡片消息的解析、类型名与展示视图统一在 `shared/cards.py`；
- **InquiryCardPool** — `fetchcard` 询盘卡（`summary.tag = "询盘"`），以 `inquiryID` 为键。类型 6 卡片消息的正文只在这里，`cards.card_view` 用消息 `extension.basicMessageInfo.bizId`（同步时存为 `content.biz_id`）查表；

会话中任意一方（买家/卖家/自动接待）的消息译文写入 CRM `Translate` 表（`shared/agent/translation.py` 经 `crm/translation_cache`），以 SDK 持久化表作为业务主存。

## 聊天数据库

目标软件的加密 IM 库（非本程序库）。设置页配置的数据目录下 `{ali_id}@icbu/database/im.sqlite`；解密见 `shared/utils/im_db_decryptor.py`。

`im_db_middleware.py`：密钥懒加载 + 带时效的解密缓存。可用条件：设置页已选身份、有对应 AES Key、解密成功。身份唯一来源为设置页手选（`app_config.json: self_ali_id`），MITM 只做联系人/profile 富化，不提供身份。同一账号的实例 profile 目录（`{ali_id}@icbu_{n}`）与规范目录视为同一身份，按最近写入解析活动源；旧 profile 冻结而新 profile 持续写入时不再误判为“新鲜”。

卡片消息（`user_content_type = 10010`）由 `shared/cards.py` 统一解析：产品类（3/2000/2028/2098）优先用 `ProductCardPool` 富化，询盘（6）用 `InquiryCardPool` 补正文，其余按类型映射字段展示，密文字段不进入 API。`shared/backend/card_sweep_service.py` 仅经 `POST /api/cards/sweep` 手动触发，逐个导航未富化产品卡所在会话，促使客户端发出 fetchcard 请求；状态经 `/api/status` 的 `workers.card-sweep` 观察。

聊天页经 CRM 读会话/消息，不直连 IM middleware。启动时后台自动读取所选账号已保存的密钥，试解密当前源库头；通过后恢复同步，不依赖页面开启。验证状态不沿用历史标记，CRM 提交完成后才恢复同步就绪。首次接入、设置变更或密钥缺失/无效时，由设置页“接入进度”显式验证。可见聊天/批量页面每 10 秒只观察已提交版本；密钥失效后停止自动刷新，所选账号的存档仍可读并提示可能过期。

启动重验只读取保存密钥，不抓取客户端进程、不迁移旧密钥文件、不操作 GUI 或调用模型。源库或密钥存储暂不可读时退避重试；缺失或不匹配的密钥等待手动处理，不删除原值。账号、目录、密钥变更、手动重试或服务停止会使旧验证任务失效。状态 GET 不触发验证。

### 接入与账号隔离

- `GET /api/settings/connection` 只观察接入状态；`POST .../connect` 连接窗口，`POST .../retry` 显式重新验证数据源并同步。前端 `AccountProvider` 在已选卖家且窗口未连接时自动调用 connect，同一窗口状态下失败后 60 秒内不重试，窗口状态说明变化时立即重试，设置页不再提供手动接入按钮。已选卖家且客户端已连接即可操作，没有单独的卖家人工确认环节。模型配置可用不等于已验证实际调用。
- `account_context.py` 用进程内 epoch 标识账号选择。目录变更清空选择；账号切换、数据源切换和重启使旧请求与任务失效。账号相关写请求须携带 `X-Account-Epoch`，连接操作在 body 中携带 epoch。
- `gui_session.py` 在入队时捕获卖家、目录、账号 epoch 和窗口代次，执行及每次 GUI 调用时重新检查。整段导航/填入/发送期间拒绝切换账号；重新接入同一窗口也使旧任务失效。状态观察不等待 GUI 运行锁。
- 窗口连接不代表自动识别客户端登录身份，仍须使用所选卖家对应的客户端；同一窗口内部换号无法自动检测。用户提交后连续搜索联系人、输入并发送，不再进行人工或截图确认。独立手工 Maa CLI 不属于 Web 进程内守卫范围。
- 同步提交固定缓存路径、卖家账号及目标 CRM 路径，旧任务只能更新原账号数据，不能发布新账号的就绪状态。切回同一存档时，旧任务晚提交可以推进存档版本，但不能恢复当前接入资格。完成回调只入队，避免阻塞唯一 CRM worker。
- `source_revision` 表示解密源快照，`revision` 表示 CRM 已提交版本；源缓存重建不能推进可读版本。前端只有在数据重读成功后解除刷新失败提示，网络暂时断开保留现有工作区和草稿。

### 同步协调与提交

- `sync_coordinator.py` 合并重复源版本，一个账号上下文同时只有一个 active，同步期间仅保留最新 pending；完成后自动补跑，失败退避重试。运行中和待处理缓存均受保护，不能被清理。
- 主库和 WAL 拷贝前后校验指纹与 CRC，拒绝不一致快照。WAL 正常复用的旧盐值尾部只从副本截去；当前有效前缀损坏不会降级成成功。临时文件不可读保留接入资格，密钥确实不匹配才要求重新验证。
- 源变化时仍完整扫描记录，在源只读事务中取得一致视图。CRM 用一个写事务提交身份、会话、消息和应用侧 `app_crm_sync_state` 元数据；消息按 500 条比较，仅写新增或变化项，保留迟到、补录和源中已消失的历史记录。
- 不支持显示的消息类型保留原字段及方向、时间，并展示占位，避免整个会话活动不可见。
- `sync_store.py` 按卖家和规范化源目录保存已提交版本、来源版本、最近成功时间与本次新增/更新/未变计数。状态读取不建表、不触发迁移或同步。
- 手动刷新检查源数据并重读当前会话，即使消息版本未变也能更新联系人资料。收件箱列表和首页统计使用独立的一致读事务；详情与 revision 观察仍不承诺属于同一个读快照。

### Agent 工具调用、回复意图与模拟会话

- SDK 的 `AgentPipeline` 返回完整调用记录（`tool_call_records`）与按轮次分组的 `tool_exchanges`；同一轮可执行多个调用，轮次上限按 LLM 调用次数计算，触顶时用 `tool_choice: none` 收尾，已有调用与意图不丢。工具失败（未知工具、非法参数、异常、超时）回传给模型继续运行；供应商拒绝强制 `tool_choice` 时自动降级为 `auto` 重试一次。
- 工具元数据（`ToolSpec`：中文名、分类、可重复次数、参数说明、超时）由宿主注册；`requires_tool` 存在 preset 与 revision 中，经追加式迁移添加，旧库默认关闭。
- `reply_customer` 是唯一的效果类工具：参数包含买家语言回复、中文参考、语气与简短理由，没有收件人；会话作用域由宿主注入的 `ToolContext` 提供。每次运行最多成功记录一条回复，参数校验失败仍允许在工具预算内修正。普通 Agent、模拟会话和实验共享这一数量约束。
- `intent_store.py` 在应用 CRM 中保存 `app_agent_intent`：live 运行写 `requested`，采纳一条时同批其余变 `discarded`；模拟只写进会话记录。提交发送时经 `origin_request_id` 关联意图并把该意图标记为 `accepted`。没有自动发送路径。
- 会话内容 schema 升到 v3：assistant 消息携带 `toolCalls` 与 `intents`，会话记录所有者（卖家 + 数据目录）、钉定版本（preset 或草稿快照）与分叉来源（会话、消息序号、冻结输入）。旧会话缺少字段时按空列表处理，首次写入补上归属。
- `POST /api/agent/fork` 用与正式运行相同的 Markdown 投影冻结截至该消息的对话，模拟会话首个输入与该时刻的正式输入逐字符一致；后续轮次 = 冻结前缀 + 本会话新增对话。模拟会话固定 `simulated` 模式，从不创建 outbox。
- `GET /api/agent/tools` 返回工具清单（名称、中文名、分类、说明、参数说明），`/api/agent/console` 不再附带。
- 读取类工具的单个结果在 `ToolExecutor` 内按 8000 字符截断：文本保留前缀并附原始长度提示，结构化结果超过上限时替换为截断说明，避免模型收到半截 JSON。截断在工具执行层完成，模型下一轮、上下文估算与会话记录看到的是同一份内容；效果类工具不截断，它们的意图在截断前已进入收集器。
- `query_product` 是第一个读取类业务工具：按 `product_id` 片段与可选标题关键词查询 `ProductCardPool`，最多返回 5 条摘要。商品池只有拦截 `fetchcard` 流量时被动写入的卡片，没有商品目录，也不区分卖家；工具描述与结果里的 `coverage` 都会说明这一点，避免把「没查到」当成「商品不存在」。

### 聊天页建议与提示词迭代

- 聊天页只选择已启用且配置 `reply_customer` 的普通 Agent，在页内面板查看建议、修改未保存提示词、复测、保存版本和插入回复。宽屏与会话并排，窄屏位于输入区上方。提示词草稿与 Agent 选择按卖家、数据目录暂存。
- 原系统“建议”保留 ID、配置和历史，转为可停用、删除的普通 Agent。新安装只初始化一次回复助手，删除后不会补种。聊天页可以从内置模板创建回复 Agent，模板不依赖现有 Agent 是否存在。
- `suggestion_runs.py` 使用受管双 worker pool 执行，`suggestion_store.py` 在宿主 CRM 的 `app_suggestion_run` 中保存请求、提示词快照、冻结会话与结果。同一请求 ID 仅执行一次，超时可读取原任务；重启中的旧任务显示中断，不自动调用模型。
- 复测沿用同一会话快照，新增消息由用户显式更新上下文。每轮预览独立运行，之前的预览既不加入聊天记录，也不进入去重黑名单。工具上限处理由回复建议这一运行用途决定，不再按 Agent ID 特判。旧同步建议接口已移除。
- 建议任务取得有效回复及简短理由后立即结束，不再请求模型收尾。必要的查询和参数修正仍可使用多个模型回合。SDK 的成功工具终止策略按运行启用，普通 Agent 独立任务保留完整收尾流程。
- 预览使用模拟意图，结果为单个 `reply`。插入接口接收空对象，不再接受候选索引，只幂等建立一个回复意图，`batch_id` 关联运行快照；发送继续使用原 outbox 采纳链路。保存提示词校验配置指纹，迟到的插入或保存不覆盖新草稿。
- 新运行快照标记 `outputProtocol=single-reply-v1`。旧运行在接口上标记 `legacy=true`、隐藏结果并禁止插入，允许显式复用冻结上下文重新生成。原始记录、旧意图和发送恢复身份保留，旧批次接受时丢弃兄弟意图的规则仍有效。提交请求及本地 pending 身份不变。接口和恢复规则见 `frontend/docs/suggestion-workbench.md`。

### 对比实验（Experiment）

- 实验是宿主概念，由 SDK 原语拼成：SDK 的 `agent_experiment` 表只存 `agent_id`、名称与不透明 JSON 内容，变体是普通 `chat_history` 行（`experiment_id` 外键列，可空）。业务含义（冻结前缀、步骤、批注、进度）全部在 `experiment_content.py` 的文档里，SDK 不解释。
- 一个实验 = 一个冻结起点 + 最多 4 个变体 + 一组步骤。变体各自钉死一个版本（当前配置、历史 revision 或编辑器草稿），创建后不可变；换版本等于加变体。步骤是统一输入，所有变体在同一步上并行执行，因此对比的是同一输入下的回答差异。
- 起点有三种：`conversation`（复用 `/api/agent/fork` 的 Markdown 投影）、`session`（继承已有模拟会话的冻结前缀与回合）、`text`（商家粘贴的外部提示词或场景）。`freezing.py` 是投影的唯一入口，两种入口不会漂移。
- 运行由受管 Worker 异步执行：`POST /api/agent/experiments/{id}/runs` 立即返回 run 快照，前端每 800ms 轮询 `GET /api/agent/runs/{id}`；同一步已有运行中的任务返回 409 并带 `runId`，前端改为观察该 run。`DELETE /api/agent/runs/{id}` 设置协作式取消标记，运行在后续回合边界检查，回合结果落库前也会检查，已完成的变体保留结果。程序正常退出时由运行时请求取消并等待，启动 Worker 失败会终结本次 run。
- 运营语义：重跑某步时先截断回该步的检查点（`progress[step].before`），买家消息只追加一次；未完成的变体可以单独补跑，不必重跑已完成的变体。运行中的变体失败不影响同一步的其他变体；任一变体失败后整次运行停止，避免后续步骤在不同输入上比较。
- 账号归属写在实验内容的 `source.owner` 里（SDK 表没有 scope 列），读取与运行都按当前卖家 + 数据目录校验。提交时捕获账号，运行及各变体开始时检查卖家与目录，发现变化则拒绝对应工作；已开始的变体绑定原账号上下文，不会把结果写进新工作区。
- 「保存为当前版本」把胜出的变体写回该 Agent 的 preset 并追加一条 revision，实验本身不改变任何运行中的 Agent。


### 出站任务与自动发送

- `outbox_store.py` 在应用 CRM 中保存 `app_outbox` 和 `app_outbox_events`，不修改通用 SDK。幂等键按卖家、目录隔离；同键不同正文或目标会拒绝，状态转换使用版本 CAS 并保留审计（事件表只记录状态转换，字段更新不再写整行快照）。`phase` 列已删除，执行子阶段由状态与 `may_have_sent` 表达。
- 发送接口返回持久化 outbox，而不是以 GUI 队列成功表示已发送。流程为 `queued → navigating → running → verifying → observed`。搜索、输入、发送共享一次 GUI 守卫，不再进行人工确认。仅填入任务在 `running` 后结束为 `filled`；`observed` 只代表本地唯一匹配，没有平台送达终态。
- 当前使用联系人 login_id 搜索及回车定位，搜索前尝试关闭低质客户免打扰弹窗。发送前先读取有效的源消息基线，不可用时在任何 GUI 输入之前失败。运行中的账号、窗口、版本仍受校验。
- 发送专用截图、有效期、复拍比较及确认接口已移除。升级时旧 `awaiting_confirmation` / `queued_send` 任务失效，不自动续发。旧截图文件、数据库中的旧截图列及审计历史保留，不在升级时删除。
- `chat_input` 只填入；单独提交发送动作前必须落库 `may_have_sent=true`。停止检查与原生任务提交共用临界区，等待在临界区外；停止后不提交新的点击。数据库临时故障仅补写结果，不重放 GUI。
- 重启不恢复旧 GUI 授权，不自动重放任何出站任务。取消只作用于 `queued`；重试只允许 `failed && !may_have_sent`。搜索、输入或发送中断的 `unknown` 不能直接重试。
- `source_messages.py` 通过 reader pin 和独立只读事务核对固定来源缓存；`send_verification.py` 对比基线后的消息 ID，要求来源、联系人、方向、正文、类型、时间窗口和任务归属均无歧义。正文比较沿用提交接口的首尾空白裁剪规则，内部空白和大小写仍须一致，候选消息与竞争任务使用同一规则，证据保留原文。结果只表示本地发现匹配消息，不表示服务端接受、送达或已读。
- 聊天页可见时每 2 秒查询任务，正文以临时卖家气泡展示。`observed` 仍显示等待同步，直到 CRM 消息的精确 externalMid 出现才合并。仅填入和取消使用输入区轻量提示，异常正文保留，详情按需展开。
- 受理回执到达后清空未改动的原草稿，等待期间的新编辑与超时草稿保留。幂等键绑定持久化草稿版本和动作，重载与超时恢复复用同一键，新草稿可以再次发送相同正文。完整接口见 `frontend/docs/stage3-outbox-contract.md`。

### 收件箱与工作台已读

- `inbox_store.py` 使用应用专属的 scope、消息台账和阅读游标表。台账与 CRM 消息、同步版本在同一个写事务提交，不修改 SDK。首次发现序号与源消息时间分开：重复同步不新增未读，迟到的新消息仍可成为未读。
- 首次完整导入不产生历史未读；历史未回复单列 `history_pending`，不计算超时。升级会保留只存在于 CRM 的旧历史，先访问页面或先同步不会改变这条规则。新目录没有可归属的旧档时，GET 不完成空基线，必须等待首次完整源同步。
- 未读仅表示本工作台的阅读状态，不是阿里平台已读。只有用户点击“标记工作台已读”才提交已加载详情的签名快照；账号 epoch、目录、会话和序号均受校验。快照之后到达的消息保持未读，阅读不清除待回复。
- 待回复依据本地记录中的人工买卖双方消息计算；系统和自动回复不清除待回复。连续催问从首条新的未回复买家消息计时，同时间戳不作为已回复的证明。无法可靠判断的方向或时间会标记不确定；GUI/outbox 状态不直接改变待回复。
- 不设回复超时阈值，也没有超时标记或到期时间。待回复会话的 `pending_since` 是首条新的未回复买家消息时间，前端据此显示已等待时长。历史待确认没有 `pending_since`。状态投影不依赖当前时间，因此无新消息时分页令牌不会变化。`inbox_revision` 通知阅读和台账变化。
- `inbox_queries.py` 在一个 SQLite 读事务中按当前目录台账筛选消息、资料和回复状态，支持历史正文/客户搜索、国家、已有资料标签、未读和待回复筛选。分页按最新时间与 SID 稳定排序，数据或到期状态变化后旧分页版本失效，前端回到第一页。
- 列表、详情、AI 上下文与导出共用当前目录的消息成员范围；首页按会话而非消息计数。SDK 仍只保存每个消息 ID 的当前正文，不具备同 ID 跨目录正文版本的完整来源历史。
- 首页面向已接入账号展示收件箱统计，未接入时保留引导。
- 客户经营画像（可维护阶段、业务标签、失败信息）使用应用扩展表按工作区隔离，支持筛选、批量维护、变更记录与 CSV 导出，见 `api/PORTRAITS.md`；AI 建议、自动重算和动态人群仍属后续迭代。

### CRM 读取边界

UI/业务以 `app.shared.crm` 为稳定入口：

- 读：`CRMAdapter.get_self_info()` / `get_user_info()` / `get_conversation_detail()`；会话列表与收件箱由 `inbox_queries` 在一致读事务中直接查询 SDK 表，路由只做投影；
- 翻译：`get_translation()` / `translation_cached()` / `get_translations()`；批量提交经 `translation_jobs.py` 在专用受管队列中异步执行，每 50 条分片，LLM 调用期间不持账号锁。任务开始前检查 epoch，账号已切换则取消，已开始的任务没有逐分片取消检查。规则与对话上下文在 `build_translation_input`，LLM I/O 使用 ≤5 字符短哈希标记，落库仍按 32 位 md5 主键写入 `Translate`。

`get_self_info(self_ali_id=None)` 按显式身份或当前手选身份读取 CRM（首次 IM 同步即写入 self 行），无回退到其他卖家。卡片池与输入草稿仍属 UI/业务缓存，不并入 CRM core。重同步勿阻塞 MITM 或 IM 解密锁，宜后台队列。

### 聊天图片

图片消息（`user_content_type = 7`）的签名 URL 只留在后端，会话详情只下发 `{label, image:{width,height}}`。图片经 `GET /api/conversations/{id}/messages/{external_mid}/image` 实时取回，不落盘。该接口使用 `OutboxObservationRoute`，不持账号锁，并用 `message_in_scope` 校验消息属于当前卖家、数据目录和会话。上游访问集中在 `shared/backend/image_fetch.py`（主机白名单、最多 3 次跳转、20MB 上限、4 并发）。文件失效返回 404，上游不可用返回 502。响应的 `Cache-Control` 由认证中间件统一设为 `no-store`。视频（`9`）只显示 `[视频]` 占位。协议细节见 `docs/api/README.md`。

### CRM 领域约定

- 业务上 `Customer` : `Account` 为 1:1（SDK 允许多 Account，本业务不拆）；`Customer` 是实体，`Account` 是其聊天账号；原始数据多在 Account 侧采集再归并到 Customer。
- 唯一平台：`Platform.pid = alibaba_icbu`。
- CRM SQLite 与 MITM/`pools.db` 分离；不要在应用侧再持久化 `user_info` / 进程内翻译缓存——资料进 `Account.extra`，译文进 SDK `Translate`。
- `AccountMapping` 使用 `ali_id` / `login_id` / `encrypt_account_id` 解析到同一 Account；先匹配再新建。请求内的别名命中不同 aid 时按身份冲突处理：不再静默重定向；资料富化跳过该条目，聊天同步沿用已提交的会话联系人，或在归属无法确定时拒绝该次源快照。
- 卖家自身也要有 Customer/Account/Mapping，`Account.extra.is_self = true`。
- 消息统一 upsert 到 SDK `Message`；`external_mid = alibaba_icbu:{self_ali_id}:{table_name}:{mid}`，跨卖家、跨表隔离；`content` 保留足够原始字段。
- `CRMAdapter` 首次访问旧库时由 `migrations.py` 自动迁移消息标识。在数据库同目录的 `backups/` 保存完整 SQLite 备份后，事务内更新标识、引用链和迁移标记。身份归属不明确、备份失败或引用损坏会回滚并阻止继续导入；不会猜测归属或删除消息。迁移无法恢复此前已被覆盖的历史内容。

## 环境变量

参见 `.env.example`。

## 日志与远程诊断

应用日志和独立 Agent 日志分别写入 `backend/data/logs/api.log`、`agent.log`，采用 JSON 行格式。请求、后台队列、同步、outbox 和 Maa 原生任务通过关联字段连接；outbox 保存首次请求和账号代次，确认及重试保留各自的执行上下文。日志不是发送或送达凭证，记录缺失不能作为未执行的依据。成功且快速的读请求只进入运行时计数，写请求、错误响应和慢读各保留一条 `http.access` 记录；第三方 HTTP 客户端日志只保留警告及以上，子进程生命周期使用 `process.*` 事件。

普通日志不记录业务正文、凭据或异常局部变量，异常保留类型和堆栈位置。应用及受管子进程日志按大小和日期轮转，并限制归档数量、体积和期限。Maa 原生文本日志保留在本机专属会话目录，属于敏感诊断资料；只在没有未完成原生任务的操作边界轮转，执行中的原生日志无法承诺严格实时大小上限。确认截图和业务审计不属于日志清理范围。

状态页区分 TCP 可达、源库成功观测、历史手工识别和后台线程进展。超过 30 秒没有成功观察源库时显示过期，保留存档及已提交版本；翻译、AI 和退出请求失联时不会据此宣布完成或自动重试。MITM 每条命中路由都记录 `count`（含 0）与 `body_bytes`，fetchcard 空响应/解析失败也留痕，用于判断抓取链路与导航触发的是哪个 API。Yak 每 5 分钟上报 `yak.traffic_tick`（seen/matched，由 Python 补时间戳）以区分「客户端流量未走代理」与「无命中关键词」；sweep 每次手动运行记录 `card.sweep_selected`（targets）与逐目标 `card.sweep_target`（sid/导航结果/未解析与已解析数），有实际扫描时记录 `card.sweep_done`，状态快照保留上一轮计数，`/api/status` 的 `pools` 只读暴露 `user_info`/`product_cards`/`inquiry_cards` 数量（未初始化时不建立池）。

开发机可经 `GET /api/app/diagnostics/download` 使用现有 Bearer token 拉取近期诊断 ZIP。接口覆盖固定关键日志来源、构建信息和被动运行摘要，不绑定所选卖家、不触发 GUI 或同步；文件经过脱敏且有读取、输出和并发上限。`manifest.json` 说明缺失、截断和省略，`runtime.json` 提供 worker 及原生日志状态。使用 `tools/pull_diagnostics.py` 下载并验证完整 ZIP，详见 [诊断接口说明](api/DIAGNOSTICS.md)。

## 应用认证

启动前必须配置固定密钥的 SHA-256 摘要。登录只传输摘要，成功后签发永久 Bearer 令牌；浏览器将令牌保存在 `localStorage`，后端只在独立 `backend/data/auth.sqlite` 中保存令牌哈希。全部业务 API 都要求认证，静态登录页面公开；API 文档入口默认关闭。更换密钥并重启会撤销旧令牌，退出登录撤销当前令牌。

登录接口在单 API 进程内全局每两秒最多接受一次尝试；普通请求、会话验证和 MITM 上报不占用登录额度。MITM 接收端口使用每次启动独立生成的内部凭据，仅接受 `POST /internal/traffic`。完整配置、API 契约、凭据撤销和部署限制见 [认证说明](api/AUTH.md)。

## 应用更新

后端更新器在受支持的安装版中每 5 分钟检查指定 Actions，发现新版本后自动下载并校验。更新包就绪后暂停定时检查，GUI、翻译、CRM 写入、实验、卡片扫描和更新检查等非常驻工作连续空闲 15 分钟后自动安装。已排队的工作和未结束的 API 写请求也阻止安装，短任务即使在两次检查之间完成也会重置计时。常驻监听、日志采集和空轮询不重置计时，但轮询正在执行时仍不能安装。

手动安装使用相同的空闲检查和互斥锁，无需等待 15 分钟。有任务运行或排队时返回 409，页面以普通弹窗说明原因；空闲时立即进入安装流程。安装锁在辅助进程启动前生效，新任务提交和触发被拒绝，定时执行暂停。握手失败或取消会释放锁，自动安装重新等待完整的 15 分钟。`MAA_UPDATE_AUTO=0` 同时关闭定时检查和自动安装，手动检查与安装仍可使用。更新接口沿用应用认证，不绑定账号 epoch。仅正式 Windows 安装版通过主启动器运行时允许安装。

启动器在拉起业务子进程之前创建 Windows Job Object 并加入，优先使用 `pwsh`，回退 `powershell` 或系统 Windows PowerShell 5.1。每次安装尝试重新解析运行时可用性，启动后消失或被替换只影响当前尝试。Artifact 只包含一个安装程序，下载阶段验证 GitHub 摘要并计算安装程序摘要。空闲检查通过后启动一次性辅助进程，由它复核并锁定安装程序。手动安装在接受响应完整发送后提交，自动安装在辅助进程接受后提交。辅助进程终止本应用的 Job，确认应用退出后覆盖安装，核对 `build-info.json` 后启动新版。业务客户端不属于该 Job。

安装完成后，设置页自动重连并展示结果。全局悬浮通知显示版本和安装执行时间，只有“知道了”的后端确认成功后才消除。确认记录保存在安装目录外，跨设备、刷新及重启生效；其他在线设备每 5 秒同步。旧通知的确认不能消除新一次安装的通知，后续安装失败也不删除尚未确认的成功通知。

已打开的页面不会自动刷新。每个导出前端记录构建标识，全局界面每 30 秒检查一次 `build-id.json`，并在重新聚焦或页面恢复时立即检查。标识不一致时，在安装完成通知所在的右下角区域显示刷新提示；确认安装通知不会关闭它，页面也不会自动重新加载。该检查从包含此功能的版本开始生效，首次升级时已打开的页面仍需手动刷新一次。

安装保留本机 `.env` 和运行数据，静默安装不由 Inno 重复启动应用。已发送但未记录结果的任务需要人工核实，更新后不自动重发消息或恢复 GUI 授权。上次安装结果记录在安装目录外；已下载的候选不跨重启保留，磁盘清理和下载空间预检规则见 [更新说明](api/UPDATES.md)。启动验证目前只核对构建身份和进程存活，不等同于 API 或 GUI 已就绪。
