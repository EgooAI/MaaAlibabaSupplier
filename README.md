<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="https://cdn.jsdelivr.net/gh/MaaAssistantArknights/design@main/logo/maa-logo_512x512.png" width="128" height="128" />
</p>

<div align="center">

# MaaAlibabaSupplier

</div>

本仓库是基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的 GUI 自动化功能，结合 Agent 技术实现的对阿里卖家（Alibaba Supplier）程序的自动化解决方案。

> **MaaFramework** 是基于图像识别技术、运用 [MAA](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 开发经验去芜存菁、完全重写的新一代自动化黑盒测试框架。
> 低代码的同时仍拥有高扩展性，旨在打造一款丰富、领先、且实用的开源库，助力开发者轻松编写出更好的黑盒测试程序，并推广普及。
> 本仓库是从[这个模板仓库](https://github.com/MaaXYZ/MaaPracticeBoilerplate)创建的。

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
