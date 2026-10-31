# MaaAlibabaSupplier

阿里卖家（Alibaba Supplier）智能客服与自动化助手。

项目把 **MITM 数据采集**、**本地 CRM**、**LLM Agent** 与 **MaaFramework 图像识别自动化** 组合成本地工作台：自动归档阿里国际站的客户会话，翻译买家消息，生成回复建议与客户分析，并在人工确认的前提下代操作客户端完成消息发送。Web 工作台由 FastAPI 后端与 Next.js 前端组成。

## 功能特性

- **收件箱与聊天工作台**：统计待回复、超时、本工作台未读、历史待确认与等待客户；会话列表按时间 / 状态 / 对话数分组与检索；消息流渲染、商品与询盘卡片展示、未读筛选、客户信息侧栏。
- **翻译与 AI 建议**：买家消息多语言翻译写入本地 CRM，支持翻译缺失重译；AI 回复建议（`reply_customer` 工具驱动）、客户意图分析、客户所处阶段分析。
- **发件箱确认**：草稿编辑与发送前确认，发送记录可追溯；工具效果按 live / simulated / 无作用域分别落库、记录或拒绝。
- **批量管理**：多选会话，维护业务阶段与业务标签、流失信息，按未读 / 待回复筛选，导出聊天记录。
- **Agent 控制台**：系统 Agent（翻译 / 建议 / 客户意图分析 / 客户所处阶段分析）、普通 Agent、Playground 调试、对比实验、多级 LLM 配置、预设版本历史与工具清单。
- **设置与系统状态**：数据目录、卖家账号、AES Key、客户端接入与同步校验；MITM 代理、接收器、任务与截图连接测试的状态观察。
- **本地 CRM SDK**：`backend/app/crm_sdk` 为独立子模块，基于 SQLModel + SQLite，可脱离宿主应用单独使用。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 后端 | Python 3.12、FastAPI、uvicorn |
| 前端 | Next.js 16、React 19、TypeScript、Ant Design 6、Tailwind CSS |
| 数据存储 | SQLite、SQLModel / SQLAlchemy，独立 `crm_sdk` 子模块 |
| 数据采集 | Yak MITM、HTTP/JSON 解析、SQLite 加密库解密 |
| GUI 自动化 | MaaFramework（Pipeline、模板匹配、Custom Action/Recognition） |
| AI | OpenAI 兼容 API、工具调用、AgentPreset、多级 LLM 参数 |

## 快速开始

### 环境要求

- Windows 10/11
- Python 3.12
- Node.js 与 pnpm（前端）
- 已安装并登录阿里卖家客户端
- [Yak](https://yaklang.com/) CLI（用于本地 MITM 代理）
- [MaaFramework](https://github.com/MaaXYZ/MaaFramework/releases) win-x64 运行时

### 安装

```bash
git clone https://github.com/EgooAI/MaaAlibabaSupplier.git
cd MaaAlibabaSupplier

# 安装前端依赖、后端依赖、MaaFramework 运行时与 Yak（含开发依赖）
python tools/install_all.py --dev

# 环境变量
copy .env.example .env
# 必填 MAA_AUTH_SECRET_SHA256：登录密钥的 SHA-256 摘要，生成方式见 .env.example
```

### 启动

后端（仓库根目录）：

```bash
python -m backend.app.main
```

启动时依次拉起 MaaFW、MITM 接收器、Yak MITM 代理、IM 同步服务与 HTTP API，默认监听 `127.0.0.1:8787`。

前端（另开终端）：

```bash
cd frontend
BACKEND_ORIGIN=http://127.0.0.1:8787 pnpm dev
```

浏览器访问 <http://127.0.0.1:3000>，使用 .env 中配置的登录密钥登录。

也可用脚本同时重启前后端预览（自动解析 `.env` 中的端口）：

```powershell
pwsh -NoProfile -File tools/restart-dev-preview.ps1
```

### 配置说明

完整变量见 [.env.example](.env.example)，常用几项：

| 变量 | 说明 |
| --- | --- |
| `MAA_AUTH_SECRET_SHA256` | 必填，登录密钥的 SHA-256 十六进制摘要 |
| `MAA_API_HOST` / `MAA_API_PORT` | 后端 API 监听地址与端口，默认 `127.0.0.1:8787` |
| `MAA_CRM_DB_PATH` | 本地 CRM 数据库路径，默认 `backend/data/crm.sqlite` |
| `MAA_POOLS_DB_PATH` | MITM 数据池数据库路径，默认 `data/pools.db` |
| `MITM_PROXY_PORT` | Yak MITM 监听端口，默认 `8084` |
| `MITM_RECEIVER_PORT` | Python 接收器端口，默认 `8085` |
| `FRONTEND_PORT` / `BACKEND_ORIGIN` | 开发预览的前端端口与后端地址 |

LLM 的 Base URL、API Key 与模型名在前端 **自动化 → LLM** 页面按 Level 维护，保存在本地 CRM 数据库。

## 项目结构

```text
backend/
  app/
    api/          # FastAPI 路由与鉴权
    shared/       # 业务层：backend、agent、crm、mitm、utils
    crm_sdk/      # 独立本地 CRM SDK（Git 子模块）
    agent/        # Maa Custom Action / Recognition
  assets/         # MaaFramework 资源：image / pipeline / model
  yak_mitm.yak    # Yak MITM 代理脚本
frontend/
  app/            # Next.js 路由
  features/       # 页面功能：聊天、批量、Agent、设置等
  components/     # 通用组件
  services/       # API 适配与登录 / 账号会话
  domain/         # 领域模型与纯函数
tools/            # 安装、打包与开发辅助脚本
docs/             # 业务与接口文档
```

更细的模块说明见 [AGENTS.md](AGENTS.md)、[backend/app/README.md](backend/app/README.md) 与 [docs/](docs/)。

## 数据与隐私

- 客户、会话、消息、译文与配置均保存在本地 SQLite，不会上传到项目方服务器。
- MITM 代理与接收器只监听 `127.0.0.1`。
- 除用户自行配置的 LLM 接口外，程序不向外部服务发送业务数据。

## 许可证

[MIT](LICENSE) © 2026 EgooAI
