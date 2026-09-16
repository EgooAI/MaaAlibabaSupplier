<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="https://cdn.jsdelivr.net/gh/MaaAssistantArknights/design@main/logo/maa-logo_512x512.png" width="128" height="128" />
</p>

<div align="center">

# MaaAlibabaSupplier

</div>

本仓库是具备 React 前端 Web UI，并在内部采用了 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的 GUI 自动化功能来自动操作阿里卖家（Alibaba Supplier）软件，结合 LLM 与 Agent 技术实现的商家询盘自动化解决方案。

> **MaaFramework** 是基于图像识别技术、运用 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 开发经验去芜存菁、完全重写的新一代自动化黑盒测试框架。
> 低代码的同时仍拥有高扩展性，旨在打造一款丰富、领先、且实用的开源库，助力开发者轻松编写出更好的黑盒测试程序，并推广普及。
> 本仓库是从[这个模板仓库](https://github.com/MaaXYZ/MaaPracticeBoilerplate)创建的。

## 项目概要

本项目的定位是阿里国际站业务员的本地 AI 沟通与客户跟进助手。主要用户是在 Windows 上使用阿里国际站客户端（即“阿里卖家”）、以中文工作的业务员或小型外贸团队负责人。多店铺、多人协作、完整订单管理暂不属于已经确定的需求。

### 业务目标

| 使用者的问题 | 本项目应提供的价值 |
|---|---|
| 海外客户语言多，看消息、写回复费时间 | 快速理解客户需求，用合适语言准确回复 |
| 客户多、聊天长，反复找上下文 | 汇总客户资料、历史沟通、询盘与产品信息 |
| 不知道优先跟进谁、下一步做什么 | 识别需求和沟通阶段，形成可执行的跟进事项 |
| 搜索联系人、输入回复等操作重复 | 在可确认、可接管的前提下代操作甚至自动操作 |
| 信息散落在阿里客户端里 | 本地保存、检索、导出，并逐步形成客户工作记录 |

## 开始开发

项目细节参见[AGENTS.md](./AGENTS.md)与[backend/app/README.md](./backend/app/README.md)。

- 环境准备（便携 Node/Python、前端依赖与构建、MaaFramework、Yak CLI）：`python tools/install_all.py`；随后复制 `.env.example` 为 `.env` 并填写。
- 后端启动：仓库根运行 `python -m backend.app.main`（MaaFW 取 `backend/deps/bin/MaaPiCli.exe`，workdir 为 `backend/assets`，无需配置）。
- 前端启动：工作目录 `frontend/`，运行 `pnpm dev`，Next.js 会将 `/api/*` 反代到后端。

## 参考资料

- [MaaFramework 相关文档](https://maafw.com/docs/1.1-QuickStarted)

- 常见问题：使用 MaaDebugger 或 MaaPicli 时弹窗报错，应用程序错误：`应用程序无法正常启动`，一般是电脑缺少某些运行库，请安装一下 [vc_redist](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

## 许可证

MIT License
