# App Codebase Overview

当前文件夹 `app/` 中，存放着本项目的主 Python 应用。它负责拉起和调度若干独立的服务，并在不同服务间进行数据协调，以实现复杂的功能逻辑。

## 代码结构

- `shared` — 主后端，`app` 与 agent 共享。含以下子包：
  - `backend/` — 业务逻辑：邮件发送（`email.py`）、数据模型（`types.py`）、IM 数据库访问（`im_chat_db.py`）、IM 数据库中间件（`im_db_middleware.py`）、聊天 AI 服务（`chat_ai.py`）、系统状态（`status.py`）。
  - `crm/` — 应用侧 CRM 适配层。`__init__.py` 是 UI/业务代码的稳定入口；`queries.py` 负责 CRM 查询；`ingest.py` 负责把旧 IM 源数据刷新进 CRM；`sync.py` 负责写入/同步 CRM SDK。
  - `llm/` — 大语言模型 API 交互（`structured_llm.py`）。
  - `utils/` — 工具：环境变量加载（`env.py`）、运行时 KV 存储（`runtime_kv.py`）、IM 数据库解密器（`im_db_decryptor.py`）、日志配置（`logging.py`）。
  - `mitm/` — MITM 共享逻辑：API 响应解析器（`parsers.py`）和数据池（`pool.py`）。
- `crm_sdk` — 通用 CRM SDK，保持可独立发布；应用专属逻辑应放在 `shared/crm/`，不要写入 SDK。
- `agent` — Maa Custom Recognition/Action 定义，被 Maa Pipeline JSON 调用的入口点。
- `web` — NiceGUI 可视化程序。`server.py` 为入口，`chat_presenter.py` 为聊天展示辅助逻辑。`pages/` 包含若干子页面，`components/` 包含若干共享组件。
- `mitm` — Yak/Yakit MITM receiver 生命周期管理（`proxy.py`），使用 `shared/mitm/` 中的解析器和池子。

## 启动流程

用户启动 `app/main.py`，启动时依次：

1. 加载 `.env` 环境变量（`load_workdir_env()`）；
2. 配置日志（`configure_logging()`）；
3. 启动 MaaFW 子进程（`maafw.start()`）；
4. 启动 MITM v4 Python receiver 线程（daemon 线程，默认监听 `127.0.0.1:8085`，接收 Yak/Yakit 转发流量）；
5. 启动 Yak MITM 代理子进程（通过 `yak yak_mitm.yak`，默认监听 `127.0.0.1:8084`）；
6. 启动 Web 服务器（主线程阻塞，NiceGUI，默认 `127.0.0.1:8787`）。

MITM receiver 以 daemon 线程运行，Web 服务器在主线程阻塞。所有组件共享内存中的池子数据。退出时会清理 MaaFW 和 Yak 子进程。

## MITM 模块设计

### 架构

当前 MITM 采用 Yak/Yakit + Python receiver 方案。Yak 是一种新的编程语言，在本项目中 Yak 脚本相当于一个可执行程序。数据流：

```text
APP -> 127.0.0.1:8084 Yak/Yakit MITM -> 127.0.0.1:8085 Python receiver -> parsers/pool
```

- `proxy.py` — Python receiver 生命周期管理，可独立运行（`python -m app.mitm.proxy`）或嵌入 `app/main.py` 线程；
- `parsers.py` — API 响应解析器，负责将原始 API 响应解析和转化为数据实例；
- `pool.py` — 线程安全的池子/缓存，其中部分支持 SQLite 持久化 + 内存缓存。

Yak/Yakit 负责 HTTPS MITM 和证书兼容性。`yak_mitm.yak` 使用 `mitm.hijackHTTPResponseEx`（非消耗式拷贝）提取响应体，POST JSON 事件到 Python receiver，不影响 APP 正常网络流。Python 端通过 `parsers.py` 解析 JSONP/JSON 响应并填充池子。

### 池子

MITM 层维护若干源数据池子/缓存，其中部分会持久化到 `data/pools.db` 并在程序启动时自动加载。业务页面不应直接读取这些池子来获取 CRM 主数据；客户、账号、会话、消息应通过 `app.shared.crm` 读取。

- **自身信息池**（`SelfInfoPool`）— 当前登录用户的信息，从 Cookie 中提取 ali_id，从 `contact.extinfo.get` API 补充详情；
- **用户信息池**（`UserInfoPool`）— 联系人信息的进程内合并缓存，从 `queryCustomerInfo`、`getuserinfobyparams`、`im.id.get`、`contact.extinfo.get` 四个 API 拦截填充，按 ali_id 索引，支持 login_id 副索引查询；持久化资料由 CRM SDK 的 `Account.extra` 承接；
- **商品卡片池**（`ProductCardPool`）— 聊天中的商品卡片，从 `fetchcard` API 拦截填充。聊天页面通过消息 `content` BLOB 中的产品 ID（`params.id`）与池子的 `productIdTitle` 关联匹配，展示图片、价格等信息；
- **通用卡片池**（`GenericCardPool`）— 非商品类卡片（RFQ、订单、反馈等），从 `fetchcard` API 拦截填充；
- **询盘卡片池**（`InquiryCardPool`）— 询盘卡片，从 `fetchcard` API 拦截填充；
- **翻译缓存**（`TranslationCache`）— 买家消息翻译结果，按文本哈希索引，仅进程内缓存，不持久化；
- **输入暂存池**（`InputPendingPool`）— 各联系人的未发送输入文本，按联系人 ID 索引，不持久化。

持久化池子提供 `put()`（写入/合并）、`get()`（查询）、`clear()`（清空持久化+内存）方法。

## 聊天数据库

下面讨论的是*目标软件的*聊天数据库。需要注意的是这个数据库不是我们程序的数据库。

### 聊天数据库文件信息

软件数据目录由环境变量 `ALIBABA_DATA_DIR` 提供（默认拼接 `IMServiceDir/MessageSDK`），下存在多个账号的消息数据库子文件夹，名字是 Ali ID + `@icbu`。

各个账号子文件夹下存在一个 `database/im.sqlite` 文件，该文件是加密的。解密方法提供于 `shared/utils/im_db_decryptor.py`，它是一种基于读取内存来进行操作的解密。

### 聊天数据库利用流程

`shared/backend` 模块中提供了 `im_db_middleware.py`，负责旧 IM 源数据库访问：隐藏加密语义，对密钥进行懒加载（请求数据库信息时如果当前没有缓存的密钥再去调用解密器来进行密钥提取），对数据库进行带时效缓存的解密拉取（设置一个例如 5s 的缓存，请求数据库信息时如果缓存过期，就执行一次“解密 -> 复制解密后的数据库到程序缓存目录”的流程）。

该中间件可用的条件包含三重：首先要求 MITM 模块的个人信息池已经获取到自己的 Ali ID，其次需要成功执行过密钥获取，最后需要保证数据库的解密拉取顺利。

聊天页面不直接调用 IM 中间件，而是调用 `app.shared.crm.refresh_chat_data()`。该 CRM ingestion 入口在内部确认旧 IM 源数据库就绪并触发同步到 CRM SDK。页面业务读取通过 `app.shared.crm` 获取会话、消息和客户资料，不直接依赖原始 IM 行对象或 IM middleware。

### CRM 读取边界

UI/业务层应把 `app.shared.crm` 包视为 CRM 稳定入口：

- 自身信息读取使用 `get_self_info()`；
- 客户资料读取使用 `get_user_info()`；
- 会话和消息读取使用 `list_conversations()`；
- 需要刷新聊天源数据时使用 `refresh_chat_data()`。

`MITM pool`、`im_db_middleware.py`、`im_chat_db.py` 仍然可以作为源数据采集/转换实现存在，但不应成为页面层依赖。商品卡片、通用卡片、询盘卡片、翻译缓存和输入草稿当前仍属于业务/UI 缓存，暂不强行并入 CRM core。

## 环境变量

参见 `.env.example` 文件。
