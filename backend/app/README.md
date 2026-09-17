# App Codebase Overview

当前文件夹 `app/`（即仓库内 `backend/app/`）中，存放着本项目的主 Python 应用。它负责拉起和调度若干独立的服务，并在不同服务间进行数据协调，以实现复杂的功能逻辑。

## 代码结构

- `shared` — 主后端，`app` 与 agent 共享。含以下子包：
  - `backend/` — 业务逻辑：邮件（`email.py`）、IM 访问（`im_chat_db.py` / `im_db_middleware.py`）、系统状态（`status.py`）。
  - `agent/` — Chat/Agent 工具层：系统 Agent 身份常量（`system_agents.py`）、输入构造（`inputs.py`）、统一执行（`runner.py`，超限抛 `AgentRunError`）、翻译写入 CRM（`translation.py`）、回复建议（`suggestions.py`）、系统预设补种（`system_presets.py`）、输出归一化（`output_normalizers.py`）。
  - `crm/` — 应用侧 CRM 适配层。`__init__.py` 是 API/业务稳定入口；`queries.py` 查询；`ingest.py` 刷新 IM→CRM；`sync.py` 写入 SDK；`translations.py` 翻译表读写。
  - `utils/` — 环境变量、运行时 KV、IM 解密、日志等。
  - `mitm/` — MITM 解析器与数据池。
- `crm_sdk` — 通用 CRM SDK（仓库内直接引用，包化改造待做）；应用专属逻辑放 `shared/crm/` 与 `shared/agent/`，不要写入 SDK。
- `agent` — Maa Custom Recognition/Action 入口。
- `api` — FastAPI：`main.py:create_app()` + `server.py:run()`；`routers/` 含 conversations / messages / status / agent / self。Web UI 见仓库根 `frontend/`（Next.js，`pnpm dev`，将 `/api/*` 反代到本服务）。
- `mitm` — Yak MITM receiver（`proxy.py`）。

## 启动流程

后端唯一启动方式：仓库根运行 `python -m backend.app.main`。启动时依次：

1. 加载 `.env`（`load_workdir_env()`，从 cwd 向上查找，兜底仓库根）；
2. 配置日志（`configure_logging()`）；
3. 播种系统 Agent（`ensure_system_agents_seeded()`）与默认 LLM 层级（`ensure_default_llm_levels_seeded()`，仅补缺失的 Level 0 空行）并注册 LLM/工具/输出归一化（幂等，仅启动时一次）；
4. 启动 MaaFW 子进程：可执行文件固定为 `backend/deps/bin/MaaPiCli.exe`（由 `tools/install_3_maafw.py` 安装），workdir 固定为 `backend/assets`（约定目录，无环境变量）；
5. 启动 MITM Python receiver 线程（`MITM_RECEIVER_HOST/PORT`，默认 `127.0.0.1:8085`）；
6. 启动 Yak MITM 代理（脚本 `backend/yak_mitm.yak`，默认 `127.0.0.1:8084`）。Yak 发现顺序：`YAK_EXECUTABLE` → PATH 上的 `yak` → `backend/.portable/yak/yak.exe`（`tools/install_4_yak.py` 的产物）；
7. 启动 IM 同步协调服务，再启动 HTTP API（主线程阻塞，uvicorn，`MAA_API_HOST/PORT` 默认 `127.0.0.1:8000`，供 `frontend/` Next.js 反代 `/api/*`）。退出时停止同步服务并等待已提交的 CRM 工作完成。

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
- **ProductCardPool / GenericCardPool / InquiryCardPool** — `fetchcard` 卡片；
- **InputPendingPool** — 旧的 SQLite 草稿池，目前聊天页不使用。聊天页草稿由浏览器按数据目录、卖家账号和会话分别保存；存储失败时保留内存副本并提示。

买家消息翻译结果写入 CRM `Translate` 表（经 `shared/agent` + `crm.translations`），以 SDK 持久化表作为业务主存。

## 聊天数据库

目标软件的加密 IM 库（非本程序库）。设置页配置的数据目录下 `{ali_id}@icbu/database/im.sqlite`；解密见 `shared/utils/im_db_decryptor.py`。

`im_db_middleware.py`：密钥懒加载 + 带时效的解密缓存。可用条件：设置页已选身份、有对应 AES Key、解密成功。身份唯一来源为设置页手选（`app_config.json: self_ali_id`），MITM 只做联系人/profile 富化，不提供身份。

聊天页经 CRM 读会话/消息，不直连 IM middleware。首次接入由设置页显式重试验证密钥和同步；验证后由后台服务检查源变化，不依赖页面开启。可见聊天/批量页面每 10 秒只观察已提交版本。密钥失效后停止自动刷新，允许继续查看所选账号的存档并提示数据可能过期。

### 接入与账号隔离

- `GET /api/settings/connection` 只观察接入状态；`POST .../connect` 连接窗口，`POST .../confirm` 记录人工确认，`POST .../retry` 重新验证数据源并同步。模型配置可用不等于已验证实际调用。
- `account_context.py` 用进程内 epoch 标识账号选择。目录变更清空选择；账号切换、数据源切换和重启使旧请求与任务失效。账号相关写请求须携带 `X-Account-Epoch`，连接操作在 body 中携带 epoch。
- `gui_session.py` 将人工确认绑定卖家、目录、账号 epoch 和窗口代次。Web GUI 操作入队和执行时均检查绑定，整段导航/填入/发送期间拒绝切换账号。状态观察不等待 GUI 运行锁。
- 人工确认不是自动识别客户端登录身份。同一窗口内自行退出或切换登录账号后，必须重新连接并确认。独立手工 Maa CLI 不属于 Web 进程内守卫范围。
- 同步提交固定缓存路径、卖家账号及目标 CRM 路径，旧任务只能更新原账号数据，不能发布新账号的就绪状态。切回同一存档时，旧任务晚提交可以推进存档版本，但不能恢复当前接入资格。完成回调只入队，避免阻塞唯一 CRM worker。
- `source_revision` 表示解密源快照，`revision` 表示 CRM 已提交版本；源缓存重建不能推进可读版本。前端只有在数据重读成功后解除刷新失败提示，网络暂时断开保留现有工作区和草稿。

### 同步协调与提交

- `sync_coordinator.py` 合并重复源版本，一个账号上下文同时只有一个 active，同步期间仅保留最新 pending；完成后自动补跑，失败退避重试。运行中和待处理缓存均受保护，不能被清理。
- 主库和 WAL 拷贝前后校验指纹与 CRC，拒绝不一致快照。WAL 正常复用的旧盐值尾部只从副本截去；当前有效前缀损坏不会降级成成功。临时文件不可读保留接入资格，密钥确实不匹配才要求重新验证。
- 源变化时仍完整扫描记录，在源只读事务中取得一致视图。CRM 用一个写事务提交身份、会话、消息和应用侧 `app_crm_sync_state` 元数据；消息按 500 条比较，仅写新增或变化项，保留迟到、补录和源中已消失的历史记录。
- 不支持显示的消息类型保留原字段及方向、时间，并展示占位，避免整个会话活动不可见。
- `sync_store.py` 按卖家和规范化源目录保存已提交版本、来源版本、最近成功时间与本次新增/更新/未变计数。状态读取不建表、不触发迁移或同步。
- 手动刷新检查源数据并重读当前会话，即使消息版本未变也能更新联系人资料。列表/详情仍由多次查询和资料补充组成，未承诺整份 API 响应与一个 revision 构成严格读快照。

### CRM 读取边界

UI/业务以 `app.shared.crm` 为稳定入口：

- `get_self_info()` / `get_user_info()` / `list_conversations()` / `refresh_chat_data()`；
- 翻译：`get_translation()` / `request_translations(texts, conversation=…)` / `translation_cached()`；user 侧规则与对话上下文在 `build_translation_input`（git 可版本化），结果仍按 `text_hash` 写入 `Translate`。

`get_self_info(self_ali_id=None)` 按显式身份或当前手选身份读取 CRM（首次 IM 同步即写入 self 行），无回退到其他卖家。卡片池与输入草稿仍属 UI/业务缓存，不并入 CRM core。重同步勿阻塞 MITM 或 IM 解密锁，宜后台队列。

### CRM 领域约定

- 业务上 `Customer` : `Account` 为 1:1（SDK 允许多 Account，本业务不拆）；`Customer` 是实体，`Account` 是其聊天账号；原始数据多在 Account 侧采集再归并到 Customer。
- 唯一平台：`Platform.pid = alibaba_icbu`。
- CRM SQLite 与 MITM/`pools.db` 分离；不要在应用侧再持久化 `user_info` / 进程内翻译缓存——资料进 `Account.extra`，译文进 SDK `Translate`。
- `AccountMapping` 使用 `ali_id` / `login_id` / `encrypt_account_id` 解析到同一 Account；先匹配再新建，后出现的 `ali_id` 应合并而非重复建号。同一 key 指向不同 aid 时，重定向到先匹配的 aid 并记录 redirect 日志。
- 卖家自身也要有 Customer/Account/Mapping，`Account.extra.is_self = true`。
- 消息统一 upsert 到 SDK `Message`；`external_mid = alibaba_icbu:{self_ali_id}:{table_name}:{mid}`，跨卖家、跨表隔离；`content` 保留足够原始字段。
- `CRMAdapter` 首次访问旧库时由 `migrations.py` 自动迁移消息标识。在数据库同目录的 `backups/` 保存完整 SQLite 备份后，事务内更新标识、引用链和迁移标记。身份归属不明确、备份失败或引用损坏会回滚并阻止继续导入；不会猜测归属或删除消息。迁移无法恢复此前已被覆盖的历史内容。

## 环境变量

参见 `.env.example`。
