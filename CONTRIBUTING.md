# 开发指南

## 目录

```text
main.py              AstrBot 插件入口、生命周期与事件钩子
core/                配置、SQLite 存储、学习、检索、模型用量和 Panel API
core/chat/           群聊上下文、持续规划、目标绑定、回复节奏与参与调度
core/memory/         人物、通用、图片与中期记忆，以及记忆控制台
core/integrations/   AstrBot 提供商之外的可选插件与服务联动
views/workshop/      原生 Panel 页面、脚本和样式
core/vendor/         保留来源清单与许可证的参考实现
.astrbot-plugin/     AstrBot 界面国际化
_conf_schema.json    AstrBot 插件配置表单
metadata.yaml        插件元数据
requirements.txt     AstrBot 安装依赖
pyproject.toml       Python 项目与开发依赖
uv.lock              可复现的开发依赖锁
tests/              原生宿主、存储、聊天与前端检查
tools/              打包、合成回放、性能分析和移植工具
docs/               当前使用文档
```

`main.py` 是唯一宿主入口；其他模块使用所属职责包的路径。运行数据放在 AstrBot 的插件数据目录，配置以宿主配置为准。SQLite 初始化直接创建当前结构，运行记录与原生配置分别维护。

## 环境

准备 Python 3.12、uv、Node.js 和相邻的 AstrBot 源码。宿主路径可以调整为自己的工作区：

```bash
uv sync --python 3.12
uv pip install -e ../AstrBot
```

`requirements.txt` 与 `pyproject.toml` 的运行依赖保持一致。密钥只通过本机环境或标准输入提供，不写入代码、测试报告或命令行参数。

## 检查

按改动范围选择检查，结构调整或发布前核验安装包：

```bash
uv run --no-sync ruff check .
uv run --no-sync pytest tests/test_release_package.py -q
node --test tests/test_frontend.mjs tests/test_panel_loading.mjs
uv run --no-sync python tools/package.py
```

完整 Python 检查入口是 `uv run --no-sync pytest`。测试中的本地包直接来自当前工作区，不安装第二份插件代码。

## 真实模型回放

`tools/native_chat_probe.py` 启动隔离的真实 AstrBot 消息队列；`tools/maibot_chat_probe.py` 用于机制参考对照。它们通过标准输入接收模型配置，在临时存储中运行合成消息，不连接 QQ。人物回放可使用 `--memory --person-memory --cases tools/chat_person_cases.json`。

报告保存到本机忽略的目录，例如 `agent/evaluation/`。合成回放不能替代真实群聊效果验收；提交只包含可复用的工具、合成输入与结论，不提交模型私有配置、原始现场聊天或运行产物。

## 发布

`tools/package.py` 只收录 Git 已跟踪的宿主入口、运行包、页面、配置、元数据、说明、图标和许可证。新增运行模块先执行 `git add`，本机临时文件不参与打包。测试、工具、开发依赖、过程报告、缓存与数据库不进入安装包。Git 源码归档使用 `.gitattributes` 的同一边界。

参考实现的原许可证与来源清单必须保留。修改自身适配层优先于修改第三方仓库。完成变更后使用 conventional commit，描述实际行为与必要验证。
