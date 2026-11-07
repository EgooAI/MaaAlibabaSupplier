---
name: using-maafw
description: Use when writing or reviewing MaaFramework JSON pipelines, custom recognition/actions, or Agent integration code.
---

<!-- This skill was last updated on 2026-11-08 -->

# Using MaaFW

MaaFramework（MaaFW）是基于视觉识别的 GUI 自动化框架，支持 JSON Pipeline 和代码方式开发。

## 文档来源与版本

本地参考文档同步自 [MaaXYZ/MaaFramework 的中文文档](https://github.com/MaaXYZ/MaaFramework/tree/d8298517762d9c673dd0474864e01e619bb17eb0/docs/zh_cn)。同步日期为 2026-11-06。正文保留上游内容，仅将指向本地未收录文档和图片的链接改为该提交的上游链接。

## 阅读顺序

先阅读 [术语解释](./1.2-术语解释.md)，再按任务选择文档：

- 编写或审查 Pipeline：阅读 [任务流水线协议](./3.1-任务流水线协议.md)，重点核对识别范围、动作目标、`next` / `on_error`、超时及回跳行为。
- 编写自定义识别、动作或 Agent：阅读 [Custom & Agent](./1.3-Custom&Agent.md) 及流水线协议中的 Custom 章节，再核对所用语言绑定的注册方法、参数和返回类型。
- 修改 `interface.json`：查阅 [ProjectInterface V2 协议](https://github.com/MaaXYZ/MaaFramework/blob/d8298517762d9c673dd0474864e01e619bb17eb0/docs/zh_cn/3.3-ProjectInterfaceV2协议.md)。
- 修改框架集成或生命周期：查阅 [集成接口一览](https://github.com/MaaXYZ/MaaFramework/blob/d8298517762d9c673dd0474864e01e619bb17eb0/docs/zh_cn/2.2-集成接口一览.md) 和 [回调协议](https://github.com/MaaXYZ/MaaFramework/blob/d8298517762d9c673dd0474864e01e619bb17eb0/docs/zh_cn/2.3-回调协议.md)。

## 本项目中的使用

- 资源和流水线位于 `backend/assets`，自定义节点位于 `backend/app/agent/nodes`，Web 进程的框架调用位于 `backend/app/shared/backend/maafw_runner.py`。先查看已有实现，再决定如何扩展。
- 本项目的 MaaFramework 和 Python 绑定固定为 5.14.3，MaaPiCli 使用该版本主包自带的程序。启动布局见 `backend/app/maafw_process.py`，安装和打包由 `tools/install_3_maafw.py`、`tools/install.py` 负责。新增字段和接口须对照实际依赖版本确认。
- 修改后使用仓库便携 Python 运行相关检查。资源校验入口为 `tools/validate_schema.py`，后端测试须在仓库根显式传入 `-c pytest.ini`。
- 合成截图测试和隔离 CLI 启动检查不代表真实客户端操作已验证。涉及窗口识别、输入或发送时，说明实际验证范围，并遵循仓库的账号隔离与发送确认约定。
