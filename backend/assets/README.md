# Assets Directory Overview

当前文件夹 `assets/` 是 MaaFW 这个 GUI 自动化框架的资源目录。

## 目录结构

- `resource` 是最核心的目录，其中包含：
  - `image` 是图片资源文件夹，大部分是目标软件的一些*模板截图*，用于模板匹配和定位。
  - `model` 是 OCR 模型的资源文件夹，一般通过初始化脚本进行配置，开发时不会涉及。
  - `pipeline` 是最重要的资源文件夹，它存放了若干 *JSON 流水线*。每个流水线文件包含若干节点，节点直接互相连接形成自动化的执行链条。
- `config` 和 `data` 是运行时生成目录，开发时一般不涉及。
- `MaaCommonAssets` 是 MaaFW 的共用资源 git submodule 文件夹，开发时不会涉及。
- `interface.json` 是 MaaFW 的*核心元数据文件*，配置了目标软件信息、默认设置等 MaaFW 的关键数据，开发时不常修改。

## 参考资料

如需了解 MaaFW 是什么、怎么用、怎么写，请检查仓库内是否存在与 MaaFW 相关的 Agents Skill 存在。如需更多信息，可访问 https://maafw.com/docs 。
