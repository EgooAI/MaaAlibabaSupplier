# App Codebase Overview

当前文件夹 `app/` 中，存放着本项目的主 Python 应用。它负责拉起和调度若干独立的服务，并在不同服务间进行数据协调，以实现复杂的功能逻辑。

## 代码结构

- `shared` — 主后端，`app` 与 agent 共享。含以下子包：
  - `backend/` — 业务逻辑：邮件（`email.py`）、IM 访问（`im_chat_db.py` / `im_db_middleware.py`）、系统状态（`status.py`）；`chat_ai.py` 仅 re-export agent 层回复建议 API。
  - `agent/` — Chat/Agent 工具层：统一跑 AgentPreset（`chat_tools.py`）、翻译写入 CRM（`translation.py`）、回复建议（`suggestions.py`）。
  - `crm/` — 应用侧 CRM 适配层。`__init__.py` 是 UI/业务稳定入口；`queries.py` 查询；`ingest.py` 刷新 IM→CRM；`sync.py` 写入 SDK；`translations.py` 翻译表读写。
  - `utils/` — 环境变量、运行时 KV、IM 解密、日志等。
  - `mitm/` — MITM 解析器与数据池。
- `crm_sdk` — 通用 CRM SDK（可独立发布）；应用专属逻辑放 `shared/crm/` 与 `shared/agent/`，不要写入 SDK。
- `agent` — Maa Custom Recognition/Action 入口。
- `web` — NiceGUI：`server.py` 入口；`pages/` 含 chat / card / status / agent 等；`components/` 共享组件。
- `mitm` — Yak MITM receiver（`proxy.py`）。

## 启动流程

用户启动 `app/main.py`，启动时依次：

1. 加载 `.env`（`load_workdir_env()`）；
2. 配置日志（`configure_logging()`）；
3. 启动 MaaFW 子进程（`maafw.start()`）；
4. 启动 MITM Python receiver 线程（默认 `127.0.0.1:8085`）；
5. 启动 Yak MITM 代理（`yak yak_mitm.yak`，默认 `127.0.0.1:8084`）；
6. 启动 Web（主线程阻塞，NiceGUI，默认 `127.0.0.1:8787`）。

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

- **SelfInfoPool** — 当前登录用户（Cookie `ali_id` + `contact.extinfo.get`）；
- **UserInfoPool** — 联系人合并缓存；
- **ProductCardPool / GenericCardPool / InquiryCardPool** — `fetchcard` 卡片；
- **InputPendingPool** — 未发送输入草稿（不持久化）。

买家消息翻译结果写入 CRM `Translate` 表（经 `shared/agent` + `crm.translations`），不再以进程内 `TranslationCache` 作为业务主存。

## 聊天数据库

目标软件的加密 IM 库（非本程序库）。`ALIBABA_DATA_DIR` 下 `{ali_id}@icbu/database/im.sqlite`；解密见 `shared/utils/im_db_decryptor.py`。

`im_db_middleware.py`：密钥懒加载 + 带时效的解密缓存。可用条件：SelfInfo 有 `ali_id`、密钥就绪、解密成功。

聊天页调用 `app.shared.crm.refresh_chat_data()` 同步后，经 CRM 读会话/消息，不直连 IM middleware。

### CRM 读取边界

UI/业务以 `app.shared.crm` 为稳定入口：

- `get_self_info()` / `get_user_info()` / `list_conversations()` / `refresh_chat_data()`；
- 翻译：`get_translation()` / `request_translations()` / `translation_cached()`。

`get_self_info()` 在 CRM 未就绪时内部可回退 `SelfInfoPool`，页面仍只调 CRM 入口。卡片池与输入草稿仍属 UI/业务缓存，不并入 CRM core。重同步勿阻塞 MITM 或 IM 解密锁，宜后台队列。

### CRM 领域约定

- 业务上 `Customer` : `Account` 为 1:1（SDK 允许多 Account，本业务不拆）；`Customer` 是实体，`Account` 是其聊天账号；原始数据多在 Account 侧采集再归并到 Customer。
- 唯一平台：`Platform.pid = alibaba_icbu`。
- CRM SQLite 与 MITM/`pools.db` 分离；不要在应用侧再持久化 `user_info` / 进程内翻译缓存——资料进 `Account.extra`，译文进 SDK `Translate`。
- `AccountMapping` 用多种 ID（`ali_id` / `login_id` / `encrypt_account_id` / `ali_member_id` / `sender_id`）解析到同一 Account；先匹配再新建，后出现的 `ali_id` 应合并而非重复建号。
- 卖家自身也要有 Customer/Account/Mapping，`Account.extra.is_self = true`。
- 消息统一 upsert 到 SDK `Message`；`external_mid = {table_name}:{mid}`（仅 `mid` 跨表不唯一）；`content` 保留足够原始字段。

## 环境变量

参见 `.env.example`。
